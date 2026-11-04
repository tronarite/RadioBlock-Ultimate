"""Huella acústica tipo Shazam/dejavu.

Busca picos de energía (constelación) en el espectrograma y genera hashes
a partir de pares de picos cercanos en tiempo/frecuencia. Dos segmentos
solo comparten hashes si contienen literalmente el mismo audio (mismo
anuncio, misma cuña) — no basta con que "suenen parecido" (mismo
locutor, tono similar): eso es lo que se quiere verificar con este
proyecto de prueba.

Cada hash lleva también el frame (tiempo local, dentro de su ventana) en
el que se originó. No es solo para saber SI dos ventanas comparten audio,
sino DÓNDE dentro de cada una — con eso, comparando muchas apariciones
del mismo patrón entre sí, se puede ir acotando con precisión creciente
en qué tramo exacto de la ventana está el anuncio, y separar dos
anuncios distintos que suenen pegados en vez de fusionarlos (ver
`align()` más abajo y `grouping._group_by_region`).
"""

from __future__ import annotations

import numpy as np
import librosa
from scipy.ndimage import maximum_filter

SR_FP = 11025
N_FFT = 1024
HOP = 512
PEAK_NEIGHBORHOOD = (15, 15)
FAN_OUT = 5
MIN_TIME_DELTA = 1
MAX_TIME_DELTA = 100

FRAME_SECONDS = HOP / SR_FP

# Marca los blobs con información de tiempo (formato nuevo) para poder
# seguir leyendo sin problemas los guardados antes de añadirla (formato
# viejo: solo un array de hashes, sin tiempo).
_MAGIC = b"\xffTF2"


def _peaks(S_db: np.ndarray) -> list[tuple[int, int]]:
    local_max = maximum_filter(S_db, size=PEAK_NEIGHBORHOOD) == S_db
    thresh = S_db.mean() + S_db.std()
    freqs, times = np.nonzero(local_max & (S_db > thresh))
    return sorted(zip(freqs.tolist(), times.tolist()), key=lambda p: p[1])


def fingerprint(pcm: np.ndarray, sr: int) -> dict[int, int]:
    """Devuelve {hash: frame_ancla}. El frame es el instante (en frames,
    no segundos — usa FRAME_SECONDS para convertir) del pico "ancla" que
    generó ese hash, dentro de la ventana analizada."""
    if pcm.size == 0:
        return {}
    if sr != SR_FP:
        pcm = librosa.resample(pcm, orig_sr=sr, target_sr=SR_FP)
    S = np.abs(librosa.stft(pcm, n_fft=N_FFT, hop_length=HOP))
    if S.size == 0:
        return {}
    S_db = librosa.amplitude_to_db(S, ref=np.max)

    hashes: dict[int, int] = {}
    pts = _peaks(S_db)
    for i, (f1, t1) in enumerate(pts):
        for f2, t2 in pts[i + 1 : i + 1 + FAN_OUT]:
            dt = t2 - t1
            if MIN_TIME_DELTA <= dt <= MAX_TIME_DELTA:
                h = ((f1 & 0x3FF) << 18) | ((f2 & 0x3FF) << 8) | (dt & 0xFF)
                if h not in hashes or t1 < hashes[h]:
                    hashes[h] = t1
    return hashes


def similarity(a: dict[int, int], b: dict[int, int]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a.keys() & b.keys())
    return inter / min(len(a), len(b))


def shared_hash_count(a: dict[int, int], b: dict[int, int]) -> int:
    """Nº de hashes idénticos entre dos huellas, en bruto (sin normalizar
    por tamaño). Con ventanas de análisis solapadas y de duración fija,
    esto separa señal de ruido mucho mejor que un ratio: el ratio se
    diluye según lo grande que sea la ventana respecto al clip repetido,
    pero un anuncio real repitiéndose comparte un nº de hashes muy por
    encima del "suelo de ruido" (coincidencias espurias entre audio
    distinto) sin importar cuánto audio "de alrededor" distinto tenga
    cada ventana. Validado con audio real de Cadena SER: pares sin
    relación comparten decenas de hashes (mediana ~44, máx ~85 en una
    muestra de 5 min); el mismo clip repetido, aunque caiga en un punto
    totalmente distinto de la ventana, comparte miles."""
    return len(a.keys() & b.keys())


def fingerprint_to_bytes(fp: dict[int, int]) -> bytes:
    if not fp:
        return _MAGIC
    arr = np.array(
        [((h & 0xFFFFFFFFFFFF) << 16) | (t & 0xFFFF) for h, t in fp.items()],
        dtype=np.uint64,
    )
    return _MAGIC + arr.tobytes()


def bytes_to_fingerprint(blob: bytes) -> dict[int, int]:
    if not blob:
        return {}
    if blob[:4] == _MAGIC:
        arr = np.frombuffer(blob[4:], dtype=np.uint64)
        return {int(v >> 16): int(v & 0xFFFF) for v in arr}
    # Formato viejo (de antes de guardar el tiempo de cada hash): solo un
    # array de hashes. Sigue sirviendo para saber SI dos ventanas
    # coinciden, pero no para estimar en qué tramo exacto.
    arr = np.frombuffer(blob, dtype=np.uint32)
    return {int(h): 0 for h in arr}


def has_timing(fp: dict[int, int]) -> bool:
    """True si esta huella viene del formato nuevo (con tiempo real por
    hash) y no del formato viejo (todos a 0, sin información)."""
    return any(t > 0 for t in fp.values())


MIN_INLIERS_ALINEACION = 5


def align(
    fp_a: dict[int, int], fp_b: dict[int, int]
) -> tuple[int, tuple[float, float], tuple[float, float]] | None:
    """Alinea dos huellas del mismo audio repetido y acota en qué tramo
    exacto de CADA ventana está — no ventana entera, solo la parte que de
    verdad coincide con la otra.

    Cada hash compartido implica un desfase (frame_ancla_a - frame_ancla_b).
    Si de verdad es el mismo audio, MUCHOS hashes coincidirán en
    prácticamente el mismo desfase (el resto son coincidencias espurias de
    hashes sueltos que no pertenecen al tramo repetido). El desfase
    mayoritario separa la señal real del ruido; los picos que lo comparten
    marcan el principio y el final del tramo en cada lado.

    Esto es la pieza clave para separar dos anuncios distintos que a veces
    suenan pegados: si una ventana contiene el final del anuncio A y el
    principio del B, comparándola por separado contra una aparición limpia
    de A y otra de B se obtienen dos desfases distintos, cada uno
    delimitando su propio tramo dentro de la ventana — no se mezclan
    porque cada uno tiene su propio pico de coincidencias.

    Devuelve (nº de hashes que confirman el desfase, rango en fp_a, rango
    en fp_b), o None si no hay coincidencia fiable.
    """
    if not has_timing(fp_a) or not has_timing(fp_b):
        return None
    shared = fp_a.keys() & fp_b.keys()
    if len(shared) < MIN_INLIERS_ALINEACION:
        return None

    offsets: dict[int, list[int]] = {}
    for h in shared:
        offsets.setdefault(fp_a[h] - fp_b[h], []).append(h)
    _, inlier_hashes = max(offsets.items(), key=lambda kv: len(kv[1]))
    if len(inlier_hashes) < MIN_INLIERS_ALINEACION:
        return None

    times_a = [fp_a[h] for h in inlier_hashes]
    times_b = [fp_b[h] for h in inlier_hashes]
    range_a = (min(times_a) * FRAME_SECONDS, max(times_a) * FRAME_SECONDS)
    range_b = (min(times_b) * FRAME_SECONDS, max(times_b) * FRAME_SECONDS)
    return (len(inlier_hashes), range_a, range_b)
