"""Extracción de features acústicos de un segmento de audio.

Produce un vector de ~30 dimensiones combinando timbre (MFCC), brillo
espectral, energía y ritmo, tal y como describe la sección "Modelo de ML"
de la especificación.
"""

import numpy as np
import librosa

N_MFCC = 13


def extract_features(y: np.ndarray, sr: int) -> np.ndarray:
    """Extrae el vector de features de una señal de audio mono.

    `y` debe ser un array 1D de muestras float32/float64 en [-1, 1].
    """
    if y.size == 0:
        raise ValueError("empty audio segment")

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=N_MFCC)
    mfcc_mean = mfcc.mean(axis=1)
    mfcc_std = mfcc.std(axis=1)

    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)[0]
    rms = librosa.feature.rms(y=y)[0]
    zcr = librosa.feature.zero_crossing_rate(y=y)[0]

    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    tempo = np.atleast_1d(tempo).astype(float)

    vector = np.concatenate(
        [
            mfcc_mean,
            mfcc_std,
            [centroid.mean(), centroid.std()],
            [rolloff.mean(), rolloff.std()],
            [rms.mean(), rms.std()],
            [zcr.mean()],
            tempo[:1],
        ]
    )
    return vector.astype(np.float64)


def features_to_bytes(vector: np.ndarray) -> bytes:
    return vector.astype(np.float64).tobytes()


def bytes_to_features(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float64)
