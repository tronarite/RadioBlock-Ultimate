"""Extracción de características acústicas para clips de radio."""

from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np

import store


FEATURE_VERSION = "1"
N_MELS = 40


def _estadisticos(valores: np.ndarray) -> np.ndarray:
    """Resume cada fila temporal con estadísticas resistentes al ruido."""
    valores = np.atleast_2d(np.asarray(valores, dtype=np.float32))
    return np.concatenate(
        (
            np.mean(valores, axis=1),
            np.std(valores, axis=1),
            np.percentile(valores, 10, axis=1),
            np.percentile(valores, 50, axis=1),
            np.percentile(valores, 90, axis=1),
        )
    ).astype(np.float32)


def extract(wav_path: str | Path) -> np.ndarray:
    """Devuelve un vector float32 de tamaño fijo para un archivo WAV de clip."""
    y, sr = librosa.load(str(wav_path), sr=store.SAMPLE_RATE, mono=True)
    if y.size == 0:
        raise ValueError(f"El audio está vacío: {wav_path}")

    # La longitud de la última ventana no altera el tamaño del vector resultante.
    n_fft = 2048
    hop = 512
    stft = librosa.stft(y, n_fft=n_fft, hop_length=hop)
    magnitud = np.abs(stft)
    potencia = magnitud**2

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=20, n_fft=n_fft, hop_length=hop)
    delta = librosa.feature.delta(mfcc)
    mel = librosa.power_to_db(
        librosa.feature.melspectrogram(S=potencia, sr=sr, n_mels=N_MELS), ref=np.max
    )
    contraste = librosa.feature.spectral_contrast(S=magnitud, sr=sr)
    rms = librosa.feature.rms(S=magnitud)[0]
    zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop)[0]
    flux = np.sqrt(np.sum(np.diff(magnitud, axis=1, prepend=magnitud[:, :1]) ** 2, axis=0))
    chroma = librosa.feature.chroma_stft(S=potencia, sr=sr)

    onset_env = librosa.onset.onset_strength(S=potencia, sr=sr, hop_length=hop)
    tempo = float(librosa.feature.tempo(onset_envelope=onset_env, sr=sr, hop_length=hop)[0])
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=sr, hop_length=hop, units="time"
    )
    intervalos = np.diff(onsets)
    regularidad = (
        float(np.std(intervalos) / (np.mean(intervalos) + 1e-6))
        if intervalos.size >= 2
        else 0.0
    )

    armonico, percusivo = librosa.effects.hpss(y)
    ratio_armonico_percusivo = float(
        np.mean(armonico**2) / (np.mean(percusivo**2) + 1e-10)
    )
    rango_dinamico = float(np.percentile(rms, 95) - np.percentile(rms, 5))
    silencios = float(np.mean(rms < max(float(np.max(rms)) * 0.1, 1e-6)))

    partes = [
        _estadisticos(mfcc),
        _estadisticos(delta),
        _estadisticos(mel),
        _estadisticos(librosa.feature.spectral_centroid(S=magnitud, sr=sr)),
        _estadisticos(librosa.feature.spectral_rolloff(S=magnitud, sr=sr)),
        _estadisticos(librosa.feature.spectral_bandwidth(S=magnitud, sr=sr)),
        _estadisticos(contraste),
        _estadisticos(zcr),
        _estadisticos(rms),
        _estadisticos(flux),
        _estadisticos(chroma),
        np.asarray([rango_dinamico, silencios, tempo, regularidad, ratio_armonico_percusivo]),
    ]
    return np.nan_to_num(np.concatenate(partes), nan=0.0, posinf=0.0, neginf=0.0).astype(
        np.float32
    )


def compute_missing(conn) -> int:
    """Calcula y persiste las características que aún son NULL; devuelve su número."""
    filas = conn.execute("SELECT id, path FROM clips WHERE features IS NULL").fetchall()
    actualizados = 0
    for fila in filas:
        ruta = store.clip_path(fila)
        try:
            caracteristicas = extract(ruta)
        except Exception as exc:  # Un clip defectuoso no bloquea el lote completo.
            print(f"No se pudo extraer clip {fila['id']} ({ruta}): {exc}", file=sys.stderr)
            continue
        conn.execute(
            "UPDATE clips SET features = ? WHERE id = ?",
            (store.pack(caracteristicas), fila["id"]),
        )
        actualizados += 1
    conn.commit()
    return actualizados
