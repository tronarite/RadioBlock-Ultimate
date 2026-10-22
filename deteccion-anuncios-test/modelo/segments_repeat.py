"""Tramos candidatos por repetición: audio que suena igual en momentos distintos.

Recorre el audio de una radio en ventanas de WIN_S s (salto HOP_S), les saca
la huella (app/fingerprint.py) y busca pares de ventanas no solapadas que
alineen >= MIN_INLIERS hashes. De cada par sale el tramo exacto que coincide
en cada ventana (fingerprint.align), que se agrupa por transitividad y se
inserta en `segments` (source='repeat').

  python segments_repeat.py --radio cadena_ser [--report]
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import numpy as np

import audio
import repeats  # carga app/fingerprint.py como repeats.fp
import store

fp = repeats.fp

WIN_S = 40
HOP_S = 20
MIN_INLIERS = 60          # hashes alineados para dar un par por repetido (calibrar con --report)
MIN_SEPARATION_S = 120    # el mismo audio contiguo no cuenta como repetición
MIN_SEG_S = 6
PAD_S = 0.5
MAX_HASH_FANOUT = 60
TOP_CANDIDATES = 10


class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


def _merge(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1.0:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def find_segments(c, radio: str, report: bool = False) -> int:
    span = audio.radio_span(c, radio)
    if not span or span[1] - span[0] < 2 * WIN_S:
        print("poco audio para buscar repeticiones")
        return 0
    starts = np.arange(span[0], span[1] - WIN_S, HOP_S)
    fps = [fp.fingerprint(audio.load_range(c, radio, float(t), float(t + WIN_S)), store.SAMPLE_RATE) for t in starts]

    index: dict[int, list[int]] = defaultdict(list)
    for i, f in enumerate(fps):
        for h in f:
            index[h].append(i)

    uf = _UF(len(fps))
    pair_ranges: list[tuple[int, int, tuple[float, float], tuple[float, float]]] = []
    inl_all: list[int] = []
    for i, f in enumerate(fps):
        votes: dict[int, int] = defaultdict(int)
        for h in f:
            ids = index[h]
            if len(ids) > MAX_HASH_FANOUT:
                continue
            for j in ids:
                if j > i and abs(starts[j] - starts[i]) >= MIN_SEPARATION_S:
                    votes[j] += 1
        for j, _ in sorted(votes.items(), key=lambda kv: -kv[1])[:TOP_CANDIDATES]:
            res = fp.align(f, fps[j])
            if not res:
                continue
            inl_all.append(res[0])
            if res[0] >= MIN_INLIERS:
                uf.union(i, j)
                pair_ranges.append((i, j, res[1], res[2]))

    if report and inl_all:
        print(f"inliers: p50={np.percentile(inl_all, 50):.0f} p90={np.percentile(inl_all, 90):.0f} "
              f"p99={np.percentile(inl_all, 99):.0f} max={max(inl_all)}; pares>=umbral={len(pair_ranges)}")

    per_win: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for i, j, ra, rb in pair_ranges:
        per_win[i].append((starts[i] + ra[0] - PAD_S, starts[i] + ra[1] + PAD_S))
        per_win[j].append((starts[j] + rb[0] - PAD_S, starts[j] + rb[1] + PAD_S))

    groups: dict[int, list[int]] = defaultdict(list)
    for w in per_win:
        groups[uf.find(w)].append(w)

    n = 0
    for root, wins in groups.items():
        segs: list[tuple[float, float]] = []
        for w in wins:
            segs += _merge(per_win[w])
        segs = [s for s in _merge(segs) if s[1] - s[0] >= MIN_SEG_S]
        if len(segs) < 2:
            continue
        key = f"{radio}:{int(starts[min(wins)])}"
        for a, b in segs:
            cur = c.execute(
                "INSERT OR IGNORE INTO segments (radio, t_start, t_end, source, group_key, n_repeats) "
                "VALUES (?,?,?,?,?,?)", (radio, float(a), float(b), "repeat", key, len(segs)),
            )
            n += cur.rowcount
    c.commit()
    print(f"[{radio}] {len(fps)} ventanas, {len(groups)} grupos repetidos, {n} tramos nuevos")
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--radio", default="cadena_ser")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    find_segments(store.conn2(), a.radio, a.report)
