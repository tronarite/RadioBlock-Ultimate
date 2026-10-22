"""Embeddings de ventanas de audio para el detector de anuncios."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
import urllib.request

import librosa
import numpy as np

import store


class Backend(Protocol):
    """Contrato común de los extractores de embeddings."""

    dimension: int

    def embed(self, pcm_batch: list[np.ndarray]) -> np.ndarray:
        """Devuelve un embedding float32 por cada ventana de entrada."""


def _estadisticos(valores: np.ndarray) -> np.ndarray:
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


def _embedding_artesanal(y: np.ndarray) -> np.ndarray:
    """La misma familia de rasgos de features.py, aplicada a una ventana."""
    n_fft = 2048
    hop = 512
    stft = librosa.stft(y, n_fft=n_fft, hop_length=hop)
    magnitud = np.abs(stft)
    potencia = magnitud**2
    mfcc = librosa.feature.mfcc(y=y, sr=store.SAMPLE_RATE, n_mfcc=20,
                                n_fft=n_fft, hop_length=hop)
    delta = librosa.feature.delta(mfcc)
    mel = librosa.power_to_db(
        librosa.feature.melspectrogram(S=potencia, sr=store.SAMPLE_RATE, n_mels=40),
        ref=np.max,
    )
    contraste = librosa.feature.spectral_contrast(S=magnitud, sr=store.SAMPLE_RATE)
    rms = librosa.feature.rms(S=magnitud)[0]
    zcr = librosa.feature.zero_crossing_rate(y, hop_length=hop)[0]
    flux = np.sqrt(np.sum(np.diff(magnitud, axis=1, prepend=magnitud[:, :1]) ** 2, axis=0))
    chroma = librosa.feature.chroma_stft(S=potencia, sr=store.SAMPLE_RATE)
    onset_env = librosa.onset.onset_strength(S=potencia, sr=store.SAMPLE_RATE, hop_length=hop)
    tempo = float(librosa.feature.tempo(onset_envelope=onset_env, sr=store.SAMPLE_RATE)[0])
    onsets = librosa.onset.onset_detect(
        onset_envelope=onset_env, sr=store.SAMPLE_RATE, hop_length=hop, units="time"
    )
    intervalos = np.diff(onsets)
    regularidad = (float(np.std(intervalos) / (np.mean(intervalos) + 1e-6))
                   if intervalos.size >= 2 else 0.0)
    armonico, percusivo = librosa.effects.hpss(y)
    ratio = float(np.mean(armonico**2) / (np.mean(percusivo**2) + 1e-10))
    rango_dinamico = float(np.percentile(rms, 95) - np.percentile(rms, 5))
    silencios = float(np.mean(rms < max(float(np.max(rms)) * 0.1, 1e-6)))
    partes = [
        _estadisticos(mfcc), _estadisticos(delta), _estadisticos(mel),
        _estadisticos(librosa.feature.spectral_centroid(S=magnitud, sr=store.SAMPLE_RATE)),
        _estadisticos(librosa.feature.spectral_rolloff(S=magnitud, sr=store.SAMPLE_RATE)),
        _estadisticos(librosa.feature.spectral_bandwidth(S=magnitud, sr=store.SAMPLE_RATE)),
        _estadisticos(contraste), _estadisticos(zcr), _estadisticos(rms),
        _estadisticos(flux), _estadisticos(chroma),
        np.asarray([rango_dinamico, silencios, tempo, regularidad, ratio]),
    ]
    return np.nan_to_num(np.concatenate(partes), nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


class HandcraftedBackend:
    """Rasgos acústicos compactos, sin modelo ni descarga."""

    dimension = 530

    def embed(self, pcm_batch: list[np.ndarray]) -> np.ndarray:
        _validar_lote(pcm_batch)
        if not pcm_batch:
            return np.empty((0, self.dimension), dtype=np.float32)
        return np.stack([_embedding_artesanal(np.asarray(x, dtype=np.float32)) for x in pcm_batch])


class PannsBackend:
    """Cnn14 de PANNs. El paquete y el checkpoint se cargan sólo al usarlo."""

    dimension = 2048
    _URL = "https://zenodo.org/records/3987831/files/Cnn14_mAP%3D0.431.pth?download=1"

    def __init__(self) -> None:
        self._tagger = None

    def _cargar(self) -> None:
        if self._tagger is not None:
            return
        try:
            from panns_inference import AudioTagging
        except ImportError as exc:
            raise RuntimeError(
                "Falta panns-inference y Torch. Instale 'panns-inference torch' en .venv."
            ) from exc
        checkpoint = Path(store.DATA_DIR) / "models" / "Cnn14_mAP=0.431.pth"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        if not checkpoint.exists():
            try:
                print(f"Descargando checkpoint PANNs en {checkpoint}...")
                urllib.request.urlretrieve(self._URL, checkpoint)
            except Exception as exc:
                raise RuntimeError(f"No se pudo descargar el checkpoint PANNs: {exc}") from exc
        self._tagger = AudioTagging(checkpoint_path=str(checkpoint), device="cpu")

    def embed(self, pcm_batch: list[np.ndarray]) -> np.ndarray:
        _validar_lote(pcm_batch)
        if not pcm_batch:
            return np.empty((0, self.dimension), dtype=np.float32)
        self._cargar()
        # Cnn14 fue entrenado a 32 kHz; la entrada pública del proyecto es 22,05 kHz.
        audio = np.stack([
            librosa.resample(np.asarray(x, dtype=np.float32), orig_sr=store.SAMPLE_RATE, target_sr=32000)
            for x in pcm_batch
        ])
        salida = self._tagger.inference(audio)
        embedding = salida[1] if isinstance(salida, tuple) else salida["embedding"]
        embedding = np.asarray(embedding, dtype=np.float32)
        if embedding.shape != (len(pcm_batch), self.dimension):
            raise RuntimeError(f"PANNs devolvió una forma inesperada: {embedding.shape}")
        return embedding


def _validar_lote(pcm_batch: list[np.ndarray]) -> None:
    esperadas = int(round(store.WINDOW_S * store.SAMPLE_RATE))
    for i, pcm in enumerate(pcm_batch):
        if np.asarray(pcm).ndim != 1 or len(pcm) != esperadas:
            raise ValueError(f"La ventana {i} debe ser mono y tener {esperadas} muestras")


def get_backend(name: str) -> Backend:
    """Crea un backend sin cargar modelos ni iniciar descargas."""
    if name == "handcrafted":
        return HandcraftedBackend()
    if name == "panns":
        return PannsBackend()
    raise ValueError(f"Backend desconocido: {name}. Use 'handcrafted' o 'panns'.")
