"""Ciclo continuo del catalogo (pensado para dejarlo corriendo en un servidor).

Trabaja sobre su propia base (data/catalogo.db) para no competir por el
bloqueo de SQLite con capture.py, que escribe en data/modelo.db. En cada
vuelta: copia los clips nuevos, busca repeticiones, actualiza el catalogo,
escanea el audio nuevo, propone huecos y deja las cifras en el log.

  python ciclo_catalogo.py --radio cadena_ser --cada 30 --caducar-dias 14
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
os.environ.setdefault("RB_DB", str(BASE / "data" / "catalogo.db"))

import audio  # noqa: E402
import catalogo  # noqa: E402
import segments_repeat  # noqa: E402
import store  # noqa: E402


def sincronizar_clips(c) -> int:
    origen = BASE / "data" / "modelo.db"
    if not origen.exists():
        return 0
    c.execute("ATTACH DATABASE ? AS cap", (str(origen),))
    try:
        cur = c.execute("INSERT OR IGNORE INTO clips(radio, timestamp, path) "
                        "SELECT radio, timestamp, path FROM cap.clips ORDER BY id")
        c.commit()
        return cur.rowcount
    finally:
        c.execute("DETACH DATABASE cap")


def vuelta(radio: str, caducar_dias: float) -> None:
    c = store.conn2()
    catalogo.ensure(c)
    n = sincronizar_clips(c)
    span = audio.radio_span(c, radio)
    if not span:
        print("sin audio todavia", flush=True)
        return
    print(f"clips nuevos={n}  audio={(span[1]-span[0])/60:.0f} min", flush=True)
    segments_repeat.find_segments(c, radio)
    catalogo.construir(c, radio)
    catalogo.caducar(c, caducar_dias)
    indice = catalogo.Indice.desde_bd(c, radio)
    desde = c.execute("SELECT MAX(t_end) FROM detections WHERE radio=?", (radio,)).fetchone()[0]
    # con entradas nuevas conviene reescanear: se hace sobre todo el audio (barato frente a la repeticion)
    dets = catalogo.escanear(c, radio, span[0], span[1], indice)
    print(f"detecciones={len(dets)} nuevas={catalogo.guardar_detecciones(c, radio, dets)}", flush=True)
    catalogo.proponer_huecos(c, radio)
    try:
        catalogo.evaluar(c, radio)
    except Exception as e:  # la evaluacion es informativa
        print(f"eval omitida: {e}", flush=True)
    c.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--cada", type=float, default=30, help="minutos entre vueltas")
    ap.add_argument("--caducar-dias", type=float, default=14)
    ap.add_argument("--una-vez", action="store_true")
    a = ap.parse_args()
    while True:
        print(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===", flush=True)
        try:
            vuelta(a.radio, a.caducar_dias)
        except Exception as e:
            print(f"error en la vuelta: {e!r}", flush=True)
        if a.una_vez:
            return
        time.sleep(a.cada * 60)


if __name__ == "__main__":
    main()
