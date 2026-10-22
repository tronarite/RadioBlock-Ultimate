"""Graba radio en directo y la trocea en clips wav de CLIP_SECONDS s.

  python capture.py --radios cadena_ser rne5 --minutes 60     (0 = indefinido)

Una llamada a ffmpeg por emisora (-f segment); un hilo vigilante registra en
la BD cada clip ya completo (el último de cada emisora, aún en escritura, se
ignora hasta que aparece el siguiente).
"""

from __future__ import annotations

import argparse
import datetime
import glob
import importlib.util
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import store

_RADIOS_PY = Path(__file__).resolve().parent.parent / "app" / "radios.py"
_spec = importlib.util.spec_from_file_location("radios_app", _RADIOS_PY)
radios_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(radios_mod)

MAX_BACKOFF = 60


def find_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    pattern = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin\ffmpeg.exe")
    hits = glob.glob(pattern, recursive=True)
    if hits:
        return hits[0]
    raise SystemExit("ffmpeg no encontrado (instala con: winget install Gyan.FFmpeg)")


def record(radio: dict, ffmpeg: str, stop: threading.Event) -> None:
    """Mantiene ffmpeg vivo para una emisora, con reconexión y backoff."""
    out_dir = store.CLIPS_DIR / radio["key"]
    out_dir.mkdir(parents=True, exist_ok=True)
    backoff = 2
    while not stop.is_set():
        # Nombre con fecha+hora de arranque de cada clip (strftime) -> inicio real.
        pattern = str(out_dir / "%Y%m%d_%H%M%S.wav")
        cmd = [
            ffmpeg, "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1",
            "-reconnect_delay_max", "10", "-i", radio["url"], "-vn", "-ac", "1",
            "-ar", str(store.SAMPLE_RATE), "-f", "segment", "-segment_time", str(store.CLIP_SECONDS),
            "-strftime", "1", "-reset_timestamps", "1", pattern,
        ]
        started = time.time()
        proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
        while proc.poll() is None and not stop.is_set():
            time.sleep(1)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if stop.is_set():
            return
        backoff = 2 if time.time() - started > 60 else min(backoff * 2, MAX_BACKOFF)
        print(f"[{radio['key']}] ffmpeg cayó, reintento en {backoff}s")
        stop.wait(backoff)


def register(stop: threading.Event, keys: list[str]) -> None:
    """Inserta en la BD los clips completos (todos menos el más reciente por emisora)."""
    conn = store.conn()
    while True:
        final = stop.is_set()
        for key in keys:
            files = sorted((store.CLIPS_DIR / key).glob("*.wav"))
            pending = files if final else files[:-1]
            for f in pending:
                rel = f"{key}/{f.name}"
                try:
                    ts = datetime.datetime.strptime(f.stem, "%Y%m%d_%H%M%S").isoformat()
                except ValueError:
                    continue
                if f.stat().st_size < 0.9 * store.CLIP_SECONDS * store.SAMPLE_RATE * 2:  # descarta clips parciales
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO clips (radio, timestamp, path) VALUES (?,?,?)", (key, ts, rel)
                )
        conn.commit()
        if final:
            return
        stop.wait(5)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radios", nargs="+", default=["cadena_ser", "rne5"])
    ap.add_argument("--minutes", type=float, default=60)
    a = ap.parse_args()

    ffmpeg = find_ffmpeg()
    sel = []
    for k in a.radios:
        if k not in radios_mod.RADIOS_BY_KEY:
            raise SystemExit(f"emisora desconocida: {k}")
        sel.append(radios_mod.RADIOS_BY_KEY[k])

    stop = threading.Event()
    threads = [threading.Thread(target=record, args=(r, ffmpeg, stop), daemon=True) for r in sel]
    reg = threading.Thread(target=register, args=(stop, [r["key"] for r in sel]))
    for t in threads + [reg]:
        t.start()
    print(f"grabando {', '.join(r['key'] for r in sel)} ({'sin límite' if not a.minutes else f'{a.minutes:g} min'}); Ctrl+C para parar")
    try:
        deadline = time.time() + a.minutes * 60 if a.minutes else None
        while deadline is None or time.time() < deadline:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    stop.set()
    for t in threads + [reg]:
        t.join(15)
    print("captura terminada")


if __name__ == "__main__":
    main()
