"""Lectura de audio continuo a partir de los clips contiguos de una radio."""

from __future__ import annotations

import datetime as dt

import numpy as np
import soundfile as sf

import store


def load_range(c, radio: str, t0: float, t1: float) -> np.ndarray:
    """PCM float32 mono a store.SAMPLE_RATE del rango [t0, t1] (epoch s).
    Los huecos (clips que faltan) se rellenan con ceros."""
    sr = store.SAMPLE_RATE
    out = np.zeros(max(0, int(round((t1 - t0) * sr))), dtype=np.float32)
    for s, row in store.clips_for_range(c, radio, t0, t1):
        pcm, fsr = sf.read(str(store.clip_path(row)), dtype="float32")
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1)
        a = int(round((s - t0) * sr))
        lo, hi = max(a, 0), min(a + len(pcm), len(out))
        if hi > lo:
            out[lo:hi] = pcm[lo - a : hi - a]
    return out


def write_wav(path, pcm: np.ndarray) -> None:
    sf.write(str(path), pcm, store.SAMPLE_RATE)


def radio_span(c, radio: str) -> tuple[float, float] | None:
    """(primer instante, último instante) de audio disponible, epoch s."""
    r = c.execute("SELECT MIN(timestamp) a, MAX(timestamp) b FROM clips WHERE radio=?", (radio,)).fetchone()
    if not r or not r["a"]:
        return None
    return (dt.datetime.fromisoformat(r["a"]).timestamp(),
            dt.datetime.fromisoformat(r["b"]).timestamp() + store.CLIP_SECONDS)
