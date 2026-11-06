"""Piloto del silenciador en vivo: escucha la emisora con un retraso y silencia los anuncios confirmados.

  python en_vivo.py [--radio cadena_ser] [--bd data/vivo.db] [--retraso 45] [--atenuar 0] [--grabar salida.wav]

La reproduccion va RETRASO s por detras del directo: asi da tiempo a reconocer un anuncio
(la huella necesita unos segundos de el) y silenciarlo desde su primer segundo. Dos anuncios
separados por menos de RELLENO_S se silencian juntos con el hueco (el bloque entero).
Solo usa anuncios confirmados (Si); la BD sale de exportar_vivo.py. No escribe en ninguna BD.
--atenuar 0.1 deja el anuncio al 10 % en vez de mudo (para oir que se ha silenciado).
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

import catalogo
import fingerprint as fp
import radios as radios_mod
import store
from capture import find_ffmpeg

SR = store.SAMPLE_RATE
WIN_S, HOP_S = 15.0, 2.5      # ventana de reconocimiento y cada cuanto se analiza
RELLENO_S = 20.0              # hueco maximo entre dos anuncios que se silencia con ellos
FUNDIDO_S = 0.05
BLOQUE_S = 0.5                # trozo que se manda al reproductor


class Directo:
    """Audio recibido (int16) con indice absoluto de muestra desde el arranque."""

    def __init__(self):
        self.lock = threading.Condition()
        self.base = 0            # muestra absoluta de buf[0]
        self.buf = np.zeros(0, dtype=np.int16)
        self.fin = False

    @property
    def n(self) -> int:
        return self.base + len(self.buf)

    def anadir(self, datos: bytes) -> None:
        with self.lock:
            self.buf = np.concatenate([self.buf, np.frombuffer(datos, dtype=np.int16)])
            self.lock.notify_all()

    def tramo(self, a: int, b: int) -> np.ndarray:
        with self.lock:
            return self.buf[max(a - self.base, 0):max(b - self.base, 0)].copy()

    def recortar(self, hasta: int) -> None:
        with self.lock:
            q = hasta - self.base
            if q > 0:
                self.buf, self.base = self.buf[q:], hasta

    def esperar(self, n: int) -> bool:
        with self.lock:
            while self.n < n and not self.fin:
                self.lock.wait(1)
            return self.n >= n


def leer(url: str, ffmpeg: str, d: Directo) -> None:
    cmd = [ffmpeg, "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
           "-i", url, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    while True:
        datos = p.stdout.read(SR)   # medio segundo (2 bytes por muestra)
        if not datos:
            break
        d.anadir(datos)
    d.fin = True
    with d.lock:
        d.lock.notify_all()
    print("el directo se ha cortado", flush=True)


class Silencios:
    """Tramos (s desde el arranque) a silenciar."""

    def __init__(self):
        self.lock = threading.Lock()
        self.tramos: list[tuple[float, float, int]] = []

    def anadir(self, a: float, b: float, eid: int) -> bool:
        with self.lock:
            for i, (x, y, e) in enumerate(self.tramos):
                if e == eid and a <= y + 2 and b >= x - 2:   # la misma emision vista en otra ventana
                    self.tramos[i] = (min(x, a), max(y, b), e)
                    return False
            self.tramos.append((a, b, eid))
            return True

    def bloques(self) -> list[tuple[float, float]]:
        with self.lock:
            iv = sorted((a, b) for a, b, _ in self.tramos)
        out: list[list[float]] = []
        for a, b in iv:
            if out and a - out[-1][1] <= RELLENO_S:
                out[-1][1] = max(out[-1][1], b)
            else:
                out.append([a, b])
        return [(a, b) for a, b in out]

    def olvidar(self, antes_de: float) -> None:
        with self.lock:
            self.tramos = [t for t in self.tramos if t[1] >= antes_de - RELLENO_S]


def analizar(d: Directo, s: Silencios, indice: catalogo.Indice, hora0: float, log) -> None:
    win, hop = int(WIN_S * SR), int(HOP_S * SR)
    fin = win
    while d.esperar(fin):
        pcm = d.tramo(fin - win, fin).astype(np.float32) / 32768.0
        t_win = (fin - win) / SR
        ventana = fp.fingerprint(pcm, SR)
        for eid, (n, off) in catalogo.votar(indice, ventana).items():
            if n < catalogo.umbral(indice, eid):
                continue
            # solo el trozo que coincide de verdad en esta ventana (una entrada larga puede ser un bloque
            # del que hoy solo suena una parte); las ventanas siguientes lo van alargando
            al = fp.align(ventana, indice.entradas[eid][0])
            if not al:
                continue
            a = t_win + al[1][0] - catalogo.PAD_INI_S
            b = t_win + al[1][1] + catalogo.PAD_FIN_S
            if s.anadir(a, b, eid):
                hora = lambda t: dt.datetime.fromtimestamp(hora0 + t).strftime("%H:%M:%S")
                print(f"  anuncio {eid} reconocido ({n} coincidencias): {hora(a)}-{hora(b)} de la emision", flush=True)
                log.write(f"{hora(a)},{hora(b)},{eid},{n}\n")
                log.flush()
        fin += hop


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--bd", default=str(store.DATA_DIR / "vivo.db"))
    ap.add_argument("--retraso", type=float, default=45.0)
    ap.add_argument("--atenuar", type=float, default=0.0, help="volumen durante el anuncio (0 = mudo)")
    ap.add_argument("--grabar", default=None, help="guarda lo que suena en este wav")
    a = ap.parse_args()

    bd = sqlite3.connect(f"file:{Path(a.bd).as_posix()}?mode=ro", uri=True)
    entradas = {i: (fp.bytes_to_fingerprint(h), dur, "activo")
                for i, h, dur in bd.execute("SELECT id, huella, dur FROM anuncios WHERE radio=?", (a.radio,))}
    if not entradas:
        raise SystemExit(f"no hay anuncios confirmados de {a.radio} en {a.bd}")
    indice = catalogo.Indice(entradas)
    ffmpeg = find_ffmpeg()
    ffplay = str(Path(ffmpeg).with_name("ffplay.exe" if ffmpeg.endswith(".exe") else "ffplay"))
    url = radios_mod.RADIOS_BY_KEY[a.radio]["url"]

    d, s = Directo(), Silencios()
    hora0 = time.time()
    log = open(store.DATA_DIR / "vivo_log.csv", "a", encoding="utf-8")
    log.write(f"# arranque {dt.datetime.now():%Y-%m-%d %H:%M:%S}: desde,hasta,entrada,coincidencias\n")
    threading.Thread(target=leer, args=(url, ffmpeg, d), daemon=True).start()
    threading.Thread(target=analizar, args=(d, s, indice, hora0, log), daemon=True).start()
    print(f"{len(entradas)} anuncios confirmados. Cargando {a.retraso:.0f} s de {a.radio} antes de sonar...", flush=True)

    player = subprocess.Popen([ffplay, "-nodisp", "-loglevel", "error", "-f", "s16le", "-ar", str(SR),
                               "-ch_layout", "mono", "-i", "-"], stdin=subprocess.PIPE)
    grab = None
    if a.grabar:
        grab = wave.open(a.grabar, "wb")
        grab.setnchannels(1); grab.setsampwidth(2); grab.setframerate(SR)
    bloque, fundido, retraso = int(BLOQUE_S * SR), int(FUNDIDO_S * SR), int(a.retraso * SR)
    pos, gan_prev, mudo = 0, 1.0, False
    try:
        while d.esperar(pos + bloque + retraso):
            t = np.arange(pos, pos + bloque) / SR
            gan = np.ones(bloque, dtype=np.float32)
            for x, y in s.bloques():
                gan[(t >= x) & (t < y)] = a.atenuar
            # fundido corto en los cambios para que no chasquee
            gan = np.convolve(np.concatenate([np.full(fundido, gan_prev), gan]), np.ones(fundido) / fundido, "valid")[1:]
            gan_prev = float(gan[-1])
            sale = (d.tramo(pos, pos + bloque).astype(np.float32) * gan[:bloque]).astype(np.int16)
            ahora_mudo = bool(gan[bloque // 2] < 0.5)
            if ahora_mudo != mudo:
                mudo = ahora_mudo
                print(f"[{dt.datetime.fromtimestamp(hora0 + pos / SR):%H:%M:%S}] "
                      f"{'SILENCIO (anuncios)' if mudo else 'vuelve la radio'}", flush=True)
            player.stdin.write(sale.tobytes())
            if grab:
                grab.writeframes(sale.tobytes())
            pos += bloque
            d.recortar(pos - int(WIN_S * SR))
            s.olvidar(pos / SR)
    except (KeyboardInterrupt, BrokenPipeError, OSError):
        pass
    finally:
        if grab:
            grab.close()
        player.kill()
        log.close()
        print("parado", flush=True)


if __name__ == "__main__":
    main()
