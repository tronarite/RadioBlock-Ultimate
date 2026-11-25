"""Servicio de una emisora: la escucha con 60 s de retraso y deja mudos los bloques de anuncios.

  python servicio.py --radio cadena_ser [--host 100.72.42.80] [--retraso 60] [--atenuar 0]
      Directo: graba los clips de 10 s (para que el ciclo aprenda), reconoce y silencia, y sirve en el puerto
      de la emisora (radios.py): /directo.mp3 (el audio), / (pagina de estado, solo informa) y /estado.json.
  python servicio.py --radio cadena_ser --fuente data/clips/cadena_ser --desde 20261106_080000 --hasta 20261106_110000
                     [--salida-wav prueba.wav] --sin-http --sin-grabar          (prueba con clips grabados)

El nucleo es `Motor` (sin hilos, red, reloj ni base de datos): recibe PCM y devuelve el PCM retrasado con
los tramos mudos que deciden las reglas (reglas.py) a partir de lo que reconoce del catalogo. La logica
de reconocimiento y de salida es la del piloto en_vivo.py. Ver docs/GUIA_IA.md, 6.4.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import json
import queue
import sqlite3
import subprocess
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np

import catalogo
import fingerprint as fp
import radios as radios_mod
import reglas
import store
from capture import find_ffmpeg

SR = store.SAMPLE_RATE
WIN_S, HOP_S = 15.0, 2.5        # ventana de reconocimiento y cada cuanto se analiza (como en_vivo.py)
BLOQUE_S, FUNDIDO_S = 0.5, 0.05
RETRASO_S = 60.0
FUSIBLE_S = 25 * 60             # maximo de silencio en cualquier hora seguida
OLVIDO_S = 15 * 60              # las detecciones que acabaron hace mas de esto ya no influyen
INDICE_DIAS = 7                 # el indice carga lo que ha sonado en los ultimos dias


def umbral_vivo(indice, eid):
    """Como catalogo.umbral, pero contando solo lo que cabe de la entrada en la ventana (defecto D9)."""
    huella, dur, _clase = indice.entradas[eid]
    return max(catalogo.MIN_INLIERS_ABS,
               int(catalogo.FRAC_ENTRADA * len(huella) * min(1.0, WIN_S / max(dur, 1e-6))))


def cargar_indice(ruta_catalogo, radio: str, ahora: float) -> catalogo.Indice:
    """Anuncios (A), programa (P) y pendientes (N, de >= 6 s) de la emisora que han sonado en los ultimos
    INDICE_DIAS dias antes de `ahora`. Solo lectura. Sin catalogo, indice vacio (suena sin silenciar)."""
    ruta = Path(ruta_catalogo)
    if not ruta.exists():
        return catalogo.Indice({})
    c = sqlite3.connect(f"file:{ruta.as_posix()}?mode=ro", uri=True, timeout=30)
    try:
        filas = c.execute("SELECT id, huella, dur, estado, confirmado FROM catalog WHERE radio=? "
                          "AND estado IN ('activo','lista_negra') AND COALESCE(last_seen, t_origen) >= ?",
                          (radio, ahora - INDICE_DIAS * 86400)).fetchall()
    finally:
        c.close()
    entradas = {}
    for eid, huella, dur, estado, confirmado in filas:
        cl = reglas.clase(estado, confirmado)
        if cl is None or (cl == "N" and dur < catalogo.MIN_DUR_ENTRADA_S):
            continue
        entradas[eid] = (fp.bytes_to_fingerprint(huella), dur, cl)
    return catalogo.Indice(entradas)


class Motor:
    """PCM int16 mono a SR -> el mismo PCM, RETRASO s despues, con los bloques de anuncios mudos.
    Tiempos en segundos desde el arranque (muestra / SR); la hora real la pone quien lo usa."""

    def __init__(self, indice, retraso_s=RETRASO_S, atenuar=0.0, al_cambiar=None, al_reconocer=None):
        self.indice = indice        # catalogo.Indice; el tercer dato de cada entrada es su clase 'A'|'P'|'N'
        self.retraso = int(retraso_s * SR)
        self.atenuar = float(atenuar)
        self.al_cambiar = al_cambiar        # al_cambiar(mudo, t_s, eids): pasa de sonar a mudo o al reves
        self.al_reconocer = al_reconocer    # al_reconocer(eid, clase, coincidencias, ini_s, fin_s): emision nueva
        self.win, self.hop = int(WIN_S * SR), int(HOP_S * SR)
        self.bloque, self.fundido = int(BLOQUE_S * SR), int(FUNDIDO_S * SR)
        self.buf = np.zeros(0, np.int16)
        self.base = 0               # muestra absoluta de buf[0]
        self.fin_ventana = self.win  # muestra en la que acaba la proxima ventana a analizar
        self.pos = 0                # proxima muestra que sale
        self.dets: list[list] = []  # [ini_s, fin_s, eid, clase]
        self.plan: list[tuple[float, float]] = []   # tramos mudos vigentes (salida de reglas)
        self.gan_prev = 1.0
        self.mudo = False
        self.mudos_hora: list[tuple[int, int]] = []  # (muestra, longitud) de los bloques que salieron mudos
        self.fusible = False

    @property
    def n(self) -> int:
        return self.base + len(self.buf)

    def _tramo(self, a: int, b: int) -> np.ndarray:
        return self.buf[max(a - self.base, 0):max(b - self.base, 0)]

    # ------------------------------------------------------------------ reconocimiento

    def _analizar(self, fin: int) -> None:
        """Una ventana [fin - win, fin]: lo que coincide con el catalogo pasa a self.dets (de en_vivo.analizar)."""
        indice = self.indice        # una sola lectura: otro hilo puede cambiarlo al recargar
        ini = max(fin - self.win, 0)
        pcm = self._tramo(ini, fin).astype(np.float32) / 32768.0
        t_win = ini / SR
        ventana = fp.fingerprint(pcm, SR)
        cambio = False
        for eid, (n, _off) in catalogo.votar(indice, ventana).items():
            if n < umbral_vivo(indice, eid):
                continue
            # solo el trozo que coincide de verdad en esta ventana (una entrada larga puede ser un bloque
            # del que hoy solo suena una parte); las ventanas siguientes lo van alargando
            huella, _dur, cl = indice.entradas[eid]
            al = fp.align(ventana, huella)
            if not al:
                continue
            a = t_win + al[1][0] - catalogo.PAD_INI_S
            b = t_win + al[1][1] + catalogo.PAD_FIN_S
            for d in self.dets:
                if d[2] == eid and a <= d[1] + 2 and b >= d[0] - 2:   # la misma emision vista en otra ventana
                    if a < d[0] or b > d[1]:
                        d[0], d[1] = min(d[0], a), max(d[1], b)
                        cambio = True
                    break
            else:
                self.dets.append([a, b, eid, cl])
                cambio = True
                if self.al_reconocer:
                    self.al_reconocer(eid, cl, n, a, b)
        limite = self.pos / SR - OLVIDO_S
        if any(d[1] < limite for d in self.dets):
            self.dets = [d for d in self.dets if d[1] >= limite]
            cambio = True
        if cambio:
            self.plan = reglas.limitar(reglas.planificar((a, b, cl) for a, b, _e, cl in self.dets))

    # ------------------------------------------------------------------ salida

    def _eids(self, t: float) -> list[int]:
        """Entradas que explican el tramo mudo que contiene t."""
        for x, y in self.plan:
            if x <= t < y:
                return sorted({d[2] for d in self.dets if d[0] < y and d[1] > x})
        return []

    def _bloque(self, largo: int) -> np.ndarray:
        """Los `largo` siguientes samples de salida (cuerpo del bucle de en_vivo.main)."""
        t = np.arange(self.pos, self.pos + largo) / SR
        gan = np.ones(largo, dtype=np.float32)
        for x, y in self.plan:
            gan[(t >= x) & (t < y)] = self.atenuar
        # fusible: nunca mas de FUSIBLE_S mudos en la ultima hora
        hora = self.pos - 3600 * SR
        self.mudos_hora = [m for m in self.mudos_hora if m[0] >= hora]
        quiere_mudo = bool(gan[largo // 2] < 0.5)
        if quiere_mudo and sum(m[1] for m in self.mudos_hora) + largo > FUSIBLE_S * SR:
            gan[:] = 1.0
            if not self.fusible and self.al_cambiar:
                self.al_cambiar(False, self.pos / SR, ["fusible"])
            self.fusible = True
        elif not quiere_mudo:
            self.fusible = False
        # fundido corto en los cambios para que no chasquee
        gan = np.convolve(np.concatenate([np.full(self.fundido, self.gan_prev, dtype=np.float32), gan]),
                          np.ones(self.fundido) / self.fundido, "valid")[1:]
        self.gan_prev = float(gan[-1])
        datos = self._tramo(self.pos, self.pos + largo).astype(np.float32)
        # rint: con ganancia 0.99999 astype truncaria y la radio no saldria identica a la entrada
        sale = np.rint(datos * gan[:len(datos)]).clip(-32768, 32767).astype(np.int16)
        ahora_mudo = bool(gan[largo // 2] < 0.5)
        if ahora_mudo:
            self.mudos_hora.append((self.pos, largo))
        if ahora_mudo != self.mudo:
            self.mudo = ahora_mudo
            if self.al_cambiar:
                self.al_cambiar(ahora_mudo, self.pos / SR, self._eids(self.pos / SR) if ahora_mudo else [])
        self.pos += largo
        return sale

    def _recortar(self) -> None:
        hasta = min(self.pos, self.fin_ventana - self.win)
        q = hasta - self.base
        if q > 0:
            self.buf, self.base = self.buf[q:], hasta

    def procesar(self, datos: bytes) -> bytes:
        """Anade PCM int16 y devuelve el PCM que ya puede salir (puede ser b'')."""
        self.buf = np.concatenate([self.buf, np.frombuffer(datos, dtype=np.int16)])
        while self.fin_ventana <= self.n:
            self._analizar(self.fin_ventana)
            self.fin_ventana += self.hop
        salida = []
        while self.pos + self.bloque + self.retraso <= self.n:
            salida.append(self._bloque(self.bloque))
        self._recortar()
        return b"".join(x.tobytes() for x in salida)

    def cerrar(self) -> bytes:
        """Fin de la fuente (solo en pruebas): analiza lo que queda y lo saca sin esperar al retraso."""
        if self.fin_ventana - self.hop < self.n:   # la cola que ninguna ventana ha visto entera
            self._analizar(self.n)
        salida = []
        while self.pos < self.n:
            salida.append(self._bloque(min(self.bloque, self.n - self.pos)))
        self._recortar()
        return b"".join(x.tobytes() for x in salida)


# ---------------------------------------------------------------------- uso

def _epoch_nombre(nombre: str) -> float:
    return dt.datetime.strptime(nombre[:15], "%Y%m%d_%H%M%S").timestamp()


def _hora(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


class Reloj:
    """Hora real de cada muestra: anclas (muestra, epoch) puestas al arrancar ffmpeg o al empezar cada clip."""

    def __init__(self) -> None:
        self.anclas: list[tuple[int, float]] = []

    def anclar(self, muestra: int, epoch: float) -> None:
        self.anclas.append((muestra, epoch))
        if len(self.anclas) > 2000:
            del self.anclas[:1000]

    def epoch(self, t_s: float) -> float:
        if not self.anclas:
            return time.time()
        m = int(t_s * SR)
        i = max(0, bisect.bisect_right([x[0] for x in self.anclas], m) - 1)
        return self.anclas[i][1] + (m - self.anclas[i][0]) / SR


class Registro:
    """Apunta cada tramo mudo terminado en data/silencios_<radio>.csv (epoch_ini,epoch_fin,segundos,ids)."""

    def __init__(self, ruta: Path, reloj: Reloj):
        self.ruta, self.reloj = ruta, reloj
        self.ini = None
        self.ids: list = []

    def al_cambiar(self, mudo: bool, t: float, eids) -> None:
        hora = _hora(self.reloj.epoch(t))
        if eids == ["fusible"]:
            print(f"[{hora}] FUSIBLE: demasiado silencio en la ultima hora, vuelve el sonido", flush=True)
            return
        if mudo:
            self.ini, self.ids = t, list(eids)
            print(f"[{hora}] SILENCIO (anuncios {self.ids})", flush=True)
            return
        print(f"[{hora}] vuelve la radio", flush=True)
        if self.ini is not None:
            a, b = self.reloj.epoch(self.ini), self.reloj.epoch(t)
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(f"{a:.1f},{b:.1f},{b - a:.1f},{' '.join(map(str, self.ids))}\n")
            self.ini = None

    def mudo_desde(self) -> float | None:
        return self.reloj.epoch(self.ini) if self.ini is not None else None


class Grabador:
    """Guarda lo que llega en clips de CLIP_SECONDS s (data/clips/<radio>/AAAAMMDD_HHMMSS.wav) y su fila en
    modelo.db, como hacia capture.py: el ciclo aprende de ellos. Una sola decodificacion (trampa 1)."""

    def __init__(self, radio: str, reloj: Reloj):
        self.radio, self.reloj = radio, reloj
        self.carpeta = store.CLIPS_DIR / radio
        self.carpeta.mkdir(parents=True, exist_ok=True)
        self.largo = store.CLIP_SECONDS * SR
        self.trozos: list[bytes] = []
        self.n = 0                  # muestras acumuladas del clip en curso
        self.inicio = 0             # muestra absoluta de su primer sample
        self.c = None               # conexion propia: se abre en el hilo de ingesta

    def descartar(self, muestra: int) -> None:
        """Al reconectar: el clip a medias no se guarda (quedaria con un salto dentro)."""
        self.trozos, self.n, self.inicio = [], 0, muestra

    def anadir(self, datos: bytes, muestra: int) -> None:
        if not self.trozos:
            self.inicio = muestra
        self.trozos.append(datos)
        self.n += len(datos) // 2
        if self.n >= self.largo:
            todo = b"".join(self.trozos)
            clip, resto = todo[:self.largo * 2], todo[self.largo * 2:]
            self._guardar(clip, self.reloj.epoch(self.inicio / SR))
            siguiente = self.inicio + self.largo
            self.trozos, self.n, self.inicio = ([resto] if resto else []), len(resto) // 2, siguiente

    def _guardar(self, clip: bytes, epoch: float) -> None:
        nombre_t = epoch
        while (f := self.carpeta / f"{dt.datetime.fromtimestamp(nombre_t):%Y%m%d_%H%M%S}.wav").exists():
            nombre_t += 1           # el nombre no se repite; el timestamp de la fila es el real
        with wave.open(str(f), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
            w.writeframes(clip)
        if self.c is None:
            self.c = store.conn()
        self.c.execute("INSERT OR IGNORE INTO clips (radio, timestamp, path) VALUES (?,?,?)",
                       (self.radio, dt.datetime.fromtimestamp(epoch).isoformat(timespec="milliseconds"),
                        f"{self.radio}/{f.name}"))
        self.c.commit()


class AudioBroadcaster:
    """Reparte los bytes de audio ya codificados a todos los clientes conectados.
    (Copiada de backend/app/worker/proxy.py, que se retira.)"""

    def __init__(self) -> None:
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def broadcast(self, chunk: bytes) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(chunk)
            except queue.Full:
                # Cliente lento: descartamos el chunk mas viejo para no bloquear el resto.
                try:
                    q.get_nowait()
                    q.put_nowait(chunk)
                except queue.Empty:
                    pass

    @property
    def n_clients(self) -> int:
        with self._lock:
            return len(self._clients)


class Codificador:
    """PCM -> MP3 con ffmpeg; un hilo reparte lo codificado. Si ffmpeg muere, se relanza al escribir."""

    def __init__(self, ffmpeg: str, reparto: AudioBroadcaster):
        self.ffmpeg, self.reparto = ffmpeg, reparto
        self.p = None
        self._lanzar()

    def _lanzar(self) -> None:
        self.p = subprocess.Popen([self.ffmpeg, "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1",
                                   "-i", "-", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3", "-"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        threading.Thread(target=self._leer, args=(self.p,), daemon=True).start()

    def _leer(self, p) -> None:
        while True:
            chunk = p.stdout.read1(4096) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
            if not chunk:
                return
            self.reparto.broadcast(chunk)

    def escribir(self, pcm: bytes) -> None:
        if not pcm:
            return
        try:
            self.p.stdin.write(pcm)
            self.p.stdin.flush()
        except (BrokenPipeError, OSError):
            print(f"[{_hora(time.time())}] el codificador MP3 se ha caido; se relanza", flush=True)
            self.cerrar()
            self._lanzar()

    def cerrar(self) -> None:
        try:
            self.p.kill()
        except Exception:
            pass


class Estado:
    """Lo que ensena la pagina: se calcula al pedirlo, leyendo en solo lectura."""

    def __init__(self, a, motor: Motor, registro: Registro, reparto: AudioBroadcaster):
        self.a, self.motor, self.registro, self.reparto = a, motor, registro, reparto
        self.recarga = None         # epoch de la ultima recarga del indice
        self.conectado = False

    def _silencios(self) -> list[list]:
        ruta = self.registro.ruta
        if not ruta.exists():
            return []
        with open(ruta, "rb") as f:
            f.seek(max(0, ruta.stat().st_size - 65536))
            lineas = f.read().decode("utf-8", "replace").splitlines()[1:]
        out = []
        for l in lineas:
            p = l.split(",")
            if len(p) >= 3:
                try:
                    out.append([float(p[0]), float(p[1]), float(p[2]), p[3] if len(p) > 3 else ""])
                except ValueError:
                    pass
        return out

    def _veredictos(self) -> list[dict]:
        try:
            r = sqlite3.connect(f"file:{(store.DATA_DIR / 'respuestas.db').as_posix()}?mode=ro", uri=True, timeout=5)
            filas = r.execute("SELECT catalog_id, resp, fuente, ts FROM respuestas ORDER BY id DESC LIMIT 200").fetchall()
            r.close()
            c = sqlite3.connect(f"file:{Path(self.a.bd).as_posix()}?mode=ro", uri=True, timeout=5)
            de_radio = {i for (i,) in c.execute("SELECT id FROM catalog WHERE radio=?", (self.a.radio,))}
            c.close()
            filas = [f for f in filas if f[0] in de_radio][:20]
            t = sqlite3.connect(f"file:{(store.DATA_DIR / 'transcripciones.db').as_posix()}?mode=ro", uri=True, timeout=5)
            textos = {}
            for f in filas:
                x = t.execute("SELECT texto FROM t WHERE clave=?", (f"e{f[0]}",)).fetchone()
                textos[f[0]] = (x[0] if x else "")[:80]
            t.close()
            return [{"id": f[0], "veredicto": f[1], "fuente": f[2], "ts": f[3], "texto": textos[f[0]]} for f in filas]
        except Exception:
            return []

    def json(self) -> bytes:
        sil = self._silencios()
        hoy = dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
        mudo_hoy = sum(max(0.0, b - max(a, hoy)) for a, b, _s, _i in sil if b > hoy)
        if self.motor.mudo and (d := self.registro.mudo_desde()):
            mudo_hoy += max(0.0, time.time() - self.a.retraso - max(d, hoy))
        clases = {"A": 0, "P": 0, "N": 0}
        for _h, _d, cl in self.motor.indice.entradas.values():
            clases[cl] = clases.get(cl, 0) + 1
        return json.dumps({
            "radio": self.a.radio, "conectado": self.conectado, "oyentes": self.reparto.n_clients,
            "suena": "silencio" if self.motor.mudo else "radio", "retraso_s": self.a.retraso,
            "minutos_mudos_hoy": round(mudo_hoy / 60, 1), "indice": clases,
            "recarga": _hora(self.recarga) if self.recarga else None,
            "silencios": [{"desde": _hora(a), "hasta": _hora(b), "segundos": s, "ids": i} for a, b, s, i in sil[-20:][::-1]],
            "veredictos": self._veredictos(),
        }, ensure_ascii=False).encode("utf-8")


PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RadioBlock</title>
<style>body{font:15px system-ui,sans-serif;margin:16px;max-width:760px;color:#222;background:#fafafa}
h1{font-size:20px}audio{width:100%}table{border-collapse:collapse;width:100%;font-size:13px}
td,th{border-bottom:1px solid #ddd;padding:3px 6px;text-align:left}.mudo{color:#b00;font-weight:bold}
.radio{color:#070;font-weight:bold}small{color:#666}</style></head><body>
<h1>RadioBlock - <span id="radio"></span></h1>
<audio controls preload="none" src="/directo.mp3"></audio>
<p>Ahora: <span id="suena"></span> <small>(con <span id="retraso"></span> s de retraso)</small><br>
Minutos en silencio hoy: <b id="hoy"></b><br>
Catalogo cargado: <span id="indice"></span> <small>(recargado <span id="recarga"></span>)</small><br>
<small>Conectado a la emisora: <span id="con"></span>; oyentes: <span id="oy"></span></small></p>
<h2>Ultimos silencios</h2><table id="sil"></table>
<h2>Ultimas decisiones del juez</h2><table id="ver"></table>
<script>
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function act(){try{const e=await (await fetch('/estado.json')).json();
radio.textContent=e.radio;suena.textContent=e.suena;suena.className=e.suena=='silencio'?'mudo':'radio';
retraso.textContent=e.retraso_s;hoy.textContent=e.minutos_mudos_hoy;
indice.textContent=e.indice.A+' anuncios, '+e.indice.P+' de programa, '+e.indice.N+' pendientes';
recarga.textContent=e.recarga||'-';con.textContent=e.conectado?'si':'no';oy.textContent=e.oyentes;
sil.innerHTML='<tr><th>desde</th><th>hasta</th><th>s</th><th>entradas</th></tr>'+e.silencios.map(s=>
'<tr><td>'+s.desde+'</td><td>'+s.hasta+'</td><td>'+s.segundos+'</td><td>'+esc(s.ids)+'</td></tr>').join('');
ver.innerHTML='<tr><th>id</th><th>veredicto</th><th>fuente</th><th>texto</th></tr>'+e.veredictos.map(v=>
'<tr><td>'+v.id+'</td><td>'+esc(v.veredicto)+'</td><td>'+esc(v.fuente)+'</td><td>'+esc(v.texto)+'</td></tr>').join('');
}catch(x){}}
act();setInterval(act,30000);
</script></body></html>"""


def servir_http(host: str, puerto: int, reparto: AudioBroadcaster, estado: Estado) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _enviar(self, tipo: str, cuerpo: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(cuerpo)

        def do_GET(self):
            ruta = self.path.split("?")[0]
            if ruta == "/":
                return self._enviar("text/html; charset=utf-8", PAGINA.encode("utf-8"))
            if ruta == "/estado.json":
                return self._enviar("application/json; charset=utf-8", estado.json())
            if ruta != "/directo.mp3":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            q = reparto.register()
            try:
                while True:
                    chunk = q.get()
                    if chunk is None:
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                reparto.unregister(q)

    limite = time.time() + 300   # al arrancar con el sistema, la IP de Tailscale puede tardar en existir
    while True:
        try:
            srv = ThreadingHTTPServer((host, puerto), Handler)
            break
        except OSError as e:
            if time.time() > limite:
                raise
            print(f"no se puede escuchar en {host}:{puerto} ({e}); reintento en 10 s", flush=True)
            time.sleep(10)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"escuchando en http://{host}:{puerto}/  (audio: /directo.mp3)", flush=True)
    return srv


def recargar_indice(a, motor: Motor, estado: Estado, cada_s: float, ahora=time.time) -> None:
    """Hilo: cada `cada_s` vuelve a leer el catalogo; si falla (base ocupada...), se queda con el anterior."""
    while True:
        time.sleep(cada_s)
        try:
            motor.indice = cargar_indice(a.bd, a.radio, ahora())
            estado.recarga = time.time()
        except Exception as e:
            print(f"[{_hora(time.time())}] recarga del indice fallida ({e!r}); sigue el anterior", flush=True)


def preparar(a, reloj: Reloj, ahora: float):
    """Indice, motor, registro y (salvo --sin-http) codificador, reparto, estado y servidor."""
    t = time.perf_counter()
    indice = cargar_indice(a.bd, a.radio, ahora)
    clases = {}
    for _h, _d, cl in indice.entradas.values():
        clases[cl] = clases.get(cl, 0) + 1
    print(f"indice: {clases or 'vacio'} en {time.perf_counter() - t:.1f} s", flush=True)
    csv = Path(a.csv) if a.csv else store.DATA_DIR / f"silencios_{a.radio}.csv"
    registro = Registro(csv, reloj)

    def al_reconocer(eid, cl, n, ini, fin):
        if cl == "A":
            print(f"  anuncio {eid} reconocido ({n} coincidencias): "
                  f"{_hora(reloj.epoch(ini))[11:]}-{_hora(reloj.epoch(fin))[11:]} de la emision", flush=True)

    motor = Motor(indice, retraso_s=a.retraso, atenuar=a.atenuar, al_cambiar=registro.al_cambiar,
                  al_reconocer=al_reconocer)
    reparto = AudioBroadcaster()
    estado = Estado(a, motor, registro, reparto)
    estado.recarga = time.time()
    cod = None
    if not a.sin_http:
        cod = Codificador(find_ffmpeg(), reparto)
        servir_http(a.host, a.puerto, reparto, estado)
    return motor, registro, estado, cod


def probar_con_clips(a) -> None:
    """--fuente: pasa por el Motor los clips grabados entre --desde y --hasta, como si fueran el directo."""
    clips = sorted(f for f in Path(a.fuente).glob("*.wav") if a.desde <= f.stem <= a.hasta)
    if not clips:
        raise SystemExit("no hay clips en ese rango")
    t0 = _epoch_nombre(clips[0].stem)
    ahora = _epoch_nombre(clips[-1].stem) + store.CLIP_SECONDS
    print(f"{len(clips)} clips desde {clips[0].stem}", flush=True)
    reloj = Reloj()     # la hora real de cada muestra sale del nombre de su clip (como en catalogo.db)
    motor, registro, estado, cod = preparar(a, reloj, ahora)
    estado.conectado = True
    sal = None
    if a.salida_wav:
        sal = wave.open(a.salida_wav, "wb")
        sal.setnchannels(1); sal.setsampwidth(2); sal.setframerate(SR)

    def emitir(pcm: bytes) -> None:
        if sal and pcm:
            sal.writeframes(pcm)
        if cod:
            cod.escribir(pcm)

    recibidas, huecos = 0, 0
    t = time.perf_counter()
    import soundfile as sf
    for f in clips:
        e = _epoch_nombre(f.stem)
        falta = int(round((e - t0) * SR)) - recibidas
        if falta > 2 * SR:    # clip(s) que no se grabaron: silencio, para que la hora siga cuadrando
            reloj.anclar(recibidas, t0 + recibidas / SR)
            emitir(motor.procesar(np.zeros(falta, np.int16).tobytes()))
            recibidas += falta
            huecos += 1
        reloj.anclar(recibidas, e)
        pcm, sr = sf.read(str(f), dtype="int16")
        assert sr == SR, f"{f.name}: {sr} Hz"
        for i in range(0, len(pcm), motor.bloque):
            emitir(motor.procesar(pcm[i:i + motor.bloque].tobytes()))
            if a.ritmo_real:
                time.sleep(BLOQUE_S)
        recibidas += len(pcm)
    emitir(motor.cerrar())
    if sal:
        sal.close()
    dur = time.perf_counter() - t
    print(f"procesadas {recibidas / SR / 3600:.2f} h en {dur / 60:.1f} min ({recibidas / SR / max(dur, 1e-9):.0f}x); "
          f"huecos rellenados: {huecos}; silencios en {registro.ruta}", flush=True)
    if cod:
        cod.cerrar()


def directo(a) -> None:
    """La emisora en directo: ingesta -> grabador + motor -> codificador -> HTTP. No termina si se cae la red."""
    url = radios_mod.RADIOS_BY_KEY[a.radio]["url"]
    ffmpeg = find_ffmpeg()
    reloj = Reloj()
    motor, registro, estado, cod = preparar(a, reloj, time.time())
    threading.Thread(target=recargar_indice, args=(a, motor, estado, a.recargar_min * 60), daemon=True).start()
    grabador = None if a.sin_grabar else Grabador(a.radio, reloj)
    recibidas, espera = 0, 2
    print(f"{a.radio}: conectando; sonara con {a.retraso:.0f} s de retraso", flush=True)
    try:
        while True:
            p = subprocess.Popen([ffmpeg, "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1",
                                  "-reconnect_delay_max", "10", "-i", url, "-vn", "-ac", "1", "-ar", str(SR),
                                  "-f", "s16le", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            arranque = time.time()
            reloj.anclar(recibidas, arranque)
            if grabador:
                grabador.descartar(recibidas)
            estado.conectado = True
            try:
                while True:
                    datos = p.stdout.read(SR)          # medio segundo (2 bytes por muestra)
                    if len(datos) % 2:
                        datos = datos[:-1]
                    if not datos:
                        break
                    if grabador:
                        grabador.anadir(datos, recibidas)
                    salida = motor.procesar(datos)
                    recibidas += len(datos) // 2
                    if cod:
                        cod.escribir(salida)
            finally:
                p.kill()
            estado.conectado = False
            espera = 2 if time.time() - arranque > 60 else min(espera * 2, 60)
            print(f"[{_hora(time.time())}] el directo se ha cortado; reintento en {espera} s", flush=True)
            time.sleep(espera)
    except KeyboardInterrupt:
        pass
    finally:
        if cod:
            cod.cerrar()
        print("parado", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--puerto", type=int, default=None, help="por defecto, el de la emisora en radios.py")
    ap.add_argument("--bd", default=str(store.DATA_DIR / "catalogo.db"))
    ap.add_argument("--retraso", type=float, default=RETRASO_S)
    ap.add_argument("--atenuar", type=float, default=0.0, help="volumen durante el anuncio (0 = mudo)")
    ap.add_argument("--recargar-min", type=float, default=10, help="minutos entre recargas del catalogo")
    ap.add_argument("--fuente", default=None, help="carpeta de clips: prueba sin conexion")
    ap.add_argument("--desde", default="00000000_000000")
    ap.add_argument("--hasta", default="99999999_999999")
    ap.add_argument("--ritmo-real", action="store_true")
    ap.add_argument("--salida-wav", default=None)
    ap.add_argument("--csv", default=None, help="registro de silencios (por defecto data/silencios_<radio>.csv)")
    ap.add_argument("--sin-grabar", action="store_true")
    ap.add_argument("--sin-http", action="store_true")
    a = ap.parse_args()
    if a.radio not in radios_mod.RADIOS_BY_KEY:
        raise SystemExit(f"emisora desconocida: {a.radio}")
    a.puerto = a.puerto or radios_mod.RADIOS_BY_KEY[a.radio]["puerto"]
    if a.fuente:
        probar_con_clips(a)
    else:
        directo(a)


if __name__ == "__main__":
    main()
