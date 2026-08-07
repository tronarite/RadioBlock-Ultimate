"""Huella acústica tipo Shazam/dejavu.

El clustering por MFCC medio (ver `model.py`, versión anterior) mide
"timbre parecido", no "es el mismo audio". Eso vale para separar música de
voz, pero NO sirve para detectar repetición real: cualquier locutor suena
parecido a sí mismo minuto a minuto, así que una tertulia larga se agrupaba
como si fuera un anuncio repitiéndose una y otra vez.

La huella acústica resuelve esto de otra forma: busca picos de energía
(constelación) en el espectrograma y genera hashes a partir de pares de
picos cercanos en tiempo/frecuencia. Dos segmentos solo comparten hashes si
contienen literalmente el mismo audio (mismo anuncio, misma cuña) — dos
frases distintas del mismo locutor, aunque suenen parecidas en timbre, no
producen los mismos picos y por tanto no coinciden.
"""

from __future__ import annotations

import numpy as np
import librosa
from scipy.ndimage import maximum_filter

SR_FP = 11025
N_FFT = 1024
HOP = 512
PEAK_NEIGHBORHOOD = (15, 15)  # (bins de frecuencia, frames de tiempo)
FAN_OUT = 5  # a cuántos picos "objetivo" se conecta cada pico "ancla"
MIN_TIME_DELTA = 1
MAX_TIME_DELTA = 100


def _peaks(S_db: np.ndarray) -> list[tuple[int, int]]:
    local_max = maximum_filter(S_db, size=PEAK_NEIGHBORHOOD) == S_db
    thresh = S_db.mean() + S_db.std()
    freqs, times = np.nonzero(local_max & (S_db > thresh))
    return sorted(zip(freqs.tolist(), times.tolist()), key=lambda p: p[1])


def fingerprint(pcm: np.ndarray, sr: int) -> set[int]:
    """Conjunto de hashes que identifican el contenido acústico exacto del
    segmento. Robusto a diferencias de volumen (se trabaja en dB) pero
    sensible al contenido: solo el mismo clip produce los mismos hashes."""
    if pcm.size == 0:
        return set()
    if sr != SR_FP:
        pcm = librosa.resample(pcm, orig_sr=sr, target_sr=SR_FP)
    S = np.abs(librosa.stft(pcm, n_fft=N_FFT, hop_length=HOP))
    if S.size == 0:
        return set()
    S_db = librosa.amplitude_to_db(S, ref=np.max)

    hashes = set()
    pts = _peaks(S_db)
    for i, (f1, t1) in enumerate(pts):
        for f2, t2 in pts[i + 1 : i + 1 + FAN_OUT]:
            dt = t2 - t1
            if MIN_TIME_DELTA <= dt <= MAX_TIME_DELTA:
                h = ((f1 & 0x3FF) << 18) | ((f2 & 0x3FF) << 8) | (dt & 0xFF)
                hashes.add(h)
    return hashes


def similarity(a: set[int], b: set[int]) -> float:
    """Coeficiente de solape: 0 = nada en común, 1 = uno contiene al otro
    por completo. Se usa el más pequeño como denominador porque dos
    grabaciones del mismo clip pueden generar cantidades distintas de
    picos según ruido/nivel, y lo que importa es si el más corto encaja
    dentro del más largo."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))


def fingerprint_to_bytes(fp: set[int]) -> bytes:
    return np.array(sorted(fp), dtype=np.uint32).tobytes()


def bytes_to_fingerprint(blob: bytes) -> set[int]:
    if not blob:
        return set()
    return set(np.frombuffer(blob, dtype=np.uint32).tolist())
