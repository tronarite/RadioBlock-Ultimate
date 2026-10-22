"""Señal de repetición: cuántas veces suena el mismo audio en otros momentos.

Reutiliza la huella acústica de ../app/fingerprint.py. Los clips son de 10 s y
caen en una rejilla fija, así que un anuncio de 30 s nunca coincide clip a clip
exacto: se cuenta como repetición cualquier otro clip (de la misma radio, a más
de MIN_SEPARATION_S) que alinee MIN_INLIERS hashes con el mismo desfase.

Es una *característica* y una fuente de candidatos para el modelo, no la
decisión final: un anuncio nuevo aún no se ha repetido, y ahí es donde el
clasificador por características (train.py) tiene que generalizar.
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

import store

_APP = Path(__file__).resolve().parent.parent / "app" / "fingerprint.py"
_spec = importlib.util.spec_from_file_location("fp_app", _APP)
fp = importlib.util.module_from_spec(_spec)
sys.modules["fp_app"] = fp
_spec.loader.exec_module(fp)

MIN_INLIERS = 25          # hashes con el mismo desfase para dar por repetido (calibrar con --report)
MIN_SEPARATION_S = 120    # un jingle partido en varios clips contiguos no cuenta como repetición
MAX_HASH_FANOUT = 60      # hashes demasiado comunes se ignoran al votar
TOP_CANDIDATES = 8


def _fingerprint(path: Path) -> dict[int, int]:
    pcm, sr = sf.read(str(path), dtype="float32")
    if pcm.ndim > 1:
        pcm = pcm.mean(axis=1)
    return fp.fingerprint(pcm, sr)


def compute(conn, lookback_hours: float | None = None, report: bool = False) -> None:
    where = ""
    if lookback_hours:
        cutoff = (datetime.datetime.now() - datetime.timedelta(hours=lookback_hours)).isoformat()
        where = f"WHERE timestamp >= '{cutoff}'"
    rows = conn.execute(f"SELECT id, radio, timestamp, path FROM clips {where} ORDER BY timestamp").fetchall()

    by_radio: dict[str, list] = defaultdict(list)
    for r in rows:
        by_radio[r["radio"]].append(r)

    for radio, clips in by_radio.items():
        fps = {r["id"]: _fingerprint(store.clip_path(r)) for r in clips}
        ts = {r["id"]: datetime.datetime.fromisoformat(r["timestamp"]).timestamp() for r in clips}

        index: dict[int, list[int]] = defaultdict(list)
        for cid, f in fps.items():
            for h in f:
                index[h].append(cid)

        counts: dict[int, int] = {}
        inl_all: list[int] = []
        for cid, f in fps.items():
            votes: dict[int, int] = defaultdict(int)
            for h in f:
                ids = index[h]
                if len(ids) > MAX_HASH_FANOUT:
                    continue
                for o in ids:
                    if o != cid and abs(ts[o] - ts[cid]) >= MIN_SEPARATION_S:
                        votes[o] += 1
            best = sorted(votes.items(), key=lambda kv: -kv[1])[:TOP_CANDIDATES]
            # apariciones distintas: se cuentan los momentos separados entre sí
            hits: list[float] = []
            for o, _ in best:
                res = fp.align(f, fps[o])
                if res:
                    inl_all.append(res[0])
                if res and res[0] >= MIN_INLIERS and all(abs(ts[o] - h) >= MIN_SEPARATION_S for h in hits):
                    hits.append(ts[o])
            counts[cid] = len(hits)

        conn.executemany("UPDATE clips SET repeat_count=? WHERE id=?", [(n, c) for c, n in counts.items()])
        conn.commit()
        n_rep = sum(1 for n in counts.values() if n > 0)
        print(f"[{radio}] {len(clips)} clips, {n_rep} con repetición ({100 * n_rep / max(1, len(clips)):.1f}%)")
        if report and inl_all:
            print(f"  inliers de alineación: p50={np.percentile(inl_all, 50):.0f} "
                  f"p90={np.percentile(inl_all, 90):.0f} p99={np.percentile(inl_all, 99):.0f}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=None, help="solo clips de las últimas N horas")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    compute(store.conn(), a.hours, a.report)
