"""Calcula embeddings solo de las ventanas dentro de tramos (etiquetados por defecto).

Mucho más barato que embed_windows.py sobre todo el audio: se usa para entrenar
con las etiquetas que hay. embed_windows.py queda para puntuar el audio entero.

  python embed_segments.py --backend panns [--todos]
"""

from __future__ import annotations

import argparse
import time

import numpy as np

import audio
import embeddings
import store


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="panns")
    ap.add_argument("--todos", action="store_true", help="también los tramos sin etiquetar")
    a = ap.parse_args()

    c = store.conn2()
    where = "" if a.todos else "WHERE label IS NOT NULL"
    segs = c.execute(f"SELECT radio, COALESCE(t_start_user, t_start) a, COALESCE(t_end_user, t_end) b "
                     f"FROM segments {where}").fetchall()
    have = {(r["radio"], round(r["t_start"], 3)) for r in c.execute(
        "SELECT radio, t_start FROM windows WHERE backend=?", (a.backend,))}
    todo: dict[str, set[float]] = {}
    for s in segs:
        t = s["a"]
        while t + store.WINDOW_S <= s["b"]:
            if (s["radio"], round(t, 3)) not in have:
                todo.setdefault(s["radio"], set()).add(round(t, 3))
            t += store.HOP_S

    be = embeddings.get_backend(a.backend)
    t0 = time.time()
    n = 0
    for radio, instantes in todo.items():
        instantes = sorted(instantes)
        for i in range(0, len(instantes), 32):
            lote = instantes[i:i + 32]
            pcm = [audio.load_range(c, radio, t, t + store.WINDOW_S) for t in lote]
            emb = be.embed(pcm)
            c.executemany("INSERT OR IGNORE INTO windows (radio, t_start, backend, emb) VALUES (?,?,?,?)",
                          [(radio, t, a.backend, store.pack(e)) for t, e in zip(lote, emb)])
            c.commit()
            n += len(lote)
    print(f"{n} ventanas nuevas en {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
