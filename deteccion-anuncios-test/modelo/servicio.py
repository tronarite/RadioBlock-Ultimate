"""Servicio de una emisora: la escucha con 60 s de retraso y deja mudos los bloques de anuncios.

  python servicio.py --radio cadena_ser [--retraso 60] [--atenuar 0]
  python servicio.py --radio cadena_ser --fuente data/clips/cadena_ser --desde 20261106_080000 --hasta 20261106_110000
                     [--salida-wav prueba.wav] --sin-http --sin-grabar          (prueba con clips grabados)

El nucleo es `Motor` (sin hilos, red, reloj ni base de datos): recibe PCM y devuelve el PCM retrasado con
los tramos mudos que deciden las reglas (reglas.py) a partir de lo que reconoce del catalogo. La logica
de reconocimiento y de salida es la del piloto en_vivo.py. Ver docs/GUIA_IA.md, 6.4.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import time
import wave
from pathlib import Path

import numpy as np

import catalogo
import fingerprint as fp
import reglas
import store

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


class Registro:
    """Apunta cada tramo mudo terminado en data/silencios_<radio>.csv (epoch_ini,epoch_fin,segundos,ids)."""

    def __init__(self, ruta: Path, epoch):
        self.ruta, self.epoch = ruta, epoch    # epoch(t_s) -> hora real
        self.ini = None
        self.ids: list = []

    def al_cambiar(self, mudo: bool, t: float, eids) -> None:
        hora = dt.datetime.fromtimestamp(self.epoch(t)).strftime("%Y-%m-%d %H:%M:%S")
        if eids == ["fusible"]:
            print(f"[{hora}] FUSIBLE: demasiado silencio en la ultima hora, vuelve el sonido", flush=True)
            return
        if mudo:
            self.ini, self.ids = t, list(eids)
            print(f"[{hora}] SILENCIO (anuncios {self.ids})", flush=True)
            return
        print(f"[{hora}] vuelve la radio", flush=True)
        if self.ini is not None:
            a, b = self.epoch(self.ini), self.epoch(t)
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(f"{a:.1f},{b:.1f},{b - a:.1f},{' '.join(map(str, self.ids))}\n")
            self.ini = None


def probar_con_clips(a) -> None:
    """--fuente: pasa por el Motor los clips grabados entre --desde y --hasta, como si fueran el directo."""
    clips = sorted(f for f in Path(a.fuente).glob("*.wav") if a.desde <= f.stem <= a.hasta)
    if not clips:
        raise SystemExit("no hay clips en ese rango")
    t0 = _epoch_nombre(clips[0].stem)
    ahora = _epoch_nombre(clips[-1].stem) + store.CLIP_SECONDS
    t = time.perf_counter()
    indice = cargar_indice(a.bd, a.radio, ahora)
    clases = {}
    for _h, _d, cl in indice.entradas.values():
        clases[cl] = clases.get(cl, 0) + 1
    print(f"indice: {clases} en {time.perf_counter() - t:.1f} s; {len(clips)} clips desde {clips[0].stem}", flush=True)
    # la hora real de cada muestra sale del nombre de su clip (como en catalogo.db: audio.load_range)
    anclas: list[tuple[int, float]] = []

    def epoch(ts: float) -> float:
        m = int(ts * SR)
        i = max(0, np.searchsorted([x[0] for x in anclas], m, side="right") - 1)
        return anclas[i][1] + (m - anclas[i][0]) / SR

    csv = Path(a.csv) if a.csv else store.DATA_DIR / f"silencios_{a.radio}.csv"
    reg = Registro(csv, epoch)

    def al_reconocer(eid, cl, n, ini, fin):
        if cl == "A":
            print(f"  anuncio {eid} reconocido ({n} coincidencias): "
                  f"{dt.datetime.fromtimestamp(epoch(ini)):%H:%M:%S}-{dt.datetime.fromtimestamp(epoch(fin)):%H:%M:%S}",
                  flush=True)

    motor = Motor(indice, retraso_s=a.retraso, atenuar=a.atenuar, al_cambiar=reg.al_cambiar, al_reconocer=al_reconocer)
    sal = None
    if a.salida_wav:
        sal = wave.open(a.salida_wav, "wb")
        sal.setnchannels(1); sal.setsampwidth(2); sal.setframerate(SR)
    recibidas, huecos = 0, 0
    t = time.perf_counter()
    import soundfile as sf
    for f in clips:
        e = _epoch_nombre(f.stem)
        falta = int(round((e - t0) * SR)) - recibidas
        if falta > 2 * SR:    # clip(s) que no se grabaron: silencio, para que la hora siga cuadrando
            anclas.append((recibidas, t0 + recibidas / SR))
            datos = np.zeros(falta, np.int16)
            recibidas += falta
            huecos += 1
            sale = motor.procesar(datos.tobytes())
            if sal:
                sal.writeframes(sale)
        anclas.append((recibidas, e))
        pcm, sr = sf.read(str(f), dtype="int16")
        assert sr == SR, f"{f.name}: {sr} Hz"
        for i in range(0, len(pcm), motor.bloque):
            sale = motor.procesar(pcm[i:i + motor.bloque].tobytes())
            if sal and sale:
                sal.writeframes(sale)
        recibidas += len(pcm)
        if a.ritmo_real:
            time.sleep(len(pcm) / SR)
    sale = motor.cerrar()
    if sal:
        sal.writeframes(sale)
        sal.close()
    dur = time.perf_counter() - t
    print(f"procesadas {recibidas / SR / 3600:.2f} h en {dur / 60:.1f} min ({recibidas / SR / max(dur, 1e-9):.0f}x); "
          f"huecos rellenados: {huecos}; silencios en {csv}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--bd", default=str(store.DATA_DIR / "catalogo.db"))
    ap.add_argument("--retraso", type=float, default=RETRASO_S)
    ap.add_argument("--atenuar", type=float, default=0.0, help="volumen durante el anuncio (0 = mudo)")
    ap.add_argument("--fuente", default=None, help="carpeta de clips: prueba sin conexion")
    ap.add_argument("--desde", default="00000000_000000")
    ap.add_argument("--hasta", default="99999999_999999")
    ap.add_argument("--ritmo-real", action="store_true")
    ap.add_argument("--salida-wav", default=None)
    ap.add_argument("--csv", default=None, help="registro de silencios (por defecto data/silencios_<radio>.csv)")
    ap.add_argument("--sin-grabar", action="store_true")
    ap.add_argument("--sin-http", action="store_true")
    a = ap.parse_args()
    if a.fuente:
        probar_con_clips(a)
        return
    raise SystemExit("el directo (ingesta, grabacion y HTTP) llega en T7; de momento solo --fuente")


if __name__ == "__main__":
    main()
