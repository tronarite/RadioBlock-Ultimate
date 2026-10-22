"""Tramos al azar del audio grabado, para poder etiquetar "no es anuncio".

La repetición solo propone candidatos que suenan a anuncio; sin ejemplos
negativos (tertulia, música, noticias) el modelo no puede aprender la diferencia.
Se evitan los tramos que solapan con otros ya existentes (repeat/model/muestra)
y los muestreados dentro de MIN_GAP_S entre sí.

  python segments_random.py --radio cadena_ser --n 30
"""

from __future__ import annotations

import argparse
import random

import audio
import store

MIN_LEN_S, MAX_LEN_S = 10, 35
MIN_GAP_S = 20


def sample(c, radio: str, n: int, seed: int | None = None) -> int:
    span = audio.radio_span(c, radio)
    if not span or span[1] - span[0] < 3 * MAX_LEN_S:
        print("poco audio")
        return 0
    rng = random.Random(seed)
    taken = [(r["t_start"], r["t_end"]) for r in c.execute(
        "SELECT t_start, t_end FROM segments WHERE radio=?", (radio,))]
    added = 0
    for _ in range(n * 40):
        if added >= n:
            break
        ln = rng.uniform(MIN_LEN_S, MAX_LEN_S)
        a = rng.uniform(span[0] + 3, span[1] - ln - 3)
        b = a + ln
        if any(a < tb + MIN_GAP_S and b > ta - MIN_GAP_S for ta, tb in taken):
            continue
        c.execute("INSERT OR IGNORE INTO segments (radio, t_start, t_end, source) VALUES (?,?,?,'muestra')",
                  (radio, a, b))
        taken.append((a, b))
        added += 1
    c.commit()
    print(f"[{radio}] {added} tramos al azar añadidos")
    return added


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=None)
    a = ap.parse_args()
    sample(store.conn2(), a.radio, a.n, a.seed)
