"""Escucha una emisora en continuo y calcula la huella acústica de
ventanas SOLAPADAS de audio (no bloques fijos independientes). Cada
emisora tiene su propio Worker (ver app/main.py) — los patrones nunca se
comparan entre emisoras distintas.

Por qué solapadas: la huella tipo Shazam solo empareja picos que caen
DENTRO de la misma ventana de análisis. Si se trocea en bloques fijos de
20s sin solape, un anuncio que la primera vez cae repartido "40%-60%"
entre dos bloques y la segunda vez cae "10%-90%" entre otros dos bloques
distintos, generará conjuntos de picos casi completamente diferentes en
cada ocasión — la huella nunca coincide, aunque el audio sea idéntico.
Es un problema de alineación, no de las huellas en sí.

Con ventanas que se solapan (cada nueva ventana comparte la mayor parte
del audio con la anterior), cualquier fragmento de duración razonable
queda contenido COMPLETO, sin cortar, dentro de alguna ventana — sea cual
sea el instante exacto en que empiece a sonar. Eso es lo que de verdad
hace que la misma cuña, sonando en dos momentos distintos y sin ninguna
sincronía entre sí, se pueda reconocer como el mismo audio.

No hay proxy de audio ni silenciado — este proyecto no bloquea nada,
solo sirve para comprobar si la detección de repetición encuentra los
anuncios reales.
"""

from __future__ import annotations

import datetime
import logging
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import numpy as np
import soundfile as sf

from app.db import SEGMENTS_DIR, insert_segmento
from app.fingerprint import fingerprint, fingerprint_to_bytes
from app.grouping import regroup_async

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050
BYTES_PER_SAMPLE = 2  # s16le mono
MAX_BACKOFF_SECONDS = 60
REGROUP_EVERY_N_WINDOWS = 8


class Worker:
    def __init__(self, radio_key: str, nombre: str, url: str, window_seconds: int, hop_seconds: int):
        self.radio_key = radio_key
        self.nombre = nombre
        self.url = url
        self.window_seconds = window_seconds
        self.hop_seconds = hop_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._windows_since_regroup = 0

        self.started_at = datetime.datetime.utcnow()
        self.n_segmentos = 0
        self.last_segment_at: datetime.datetime | None = None
        self.connected = False

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run_loop(self) -> None:
        backoff = 1
        while not self._stop_event.is_set():
            connect_start = time.monotonic()
            try:
                self.connected = True
                self._stream_once()
                backoff = 1
            except Exception:
                logger.exception("error escuchando el stream")
            self.connected = False
            elapsed = time.monotonic() - connect_start
            logger.warning("stream cortado tras %.1fs conectado, reintentando en %ss", elapsed, backoff)
            if self._stop_event.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    def _stream_once(self) -> None:
        hop_bytes = self.hop_seconds * SAMPLE_RATE * BYTES_PER_SAMPLE
        window_samples = self.window_seconds * SAMPLE_RATE
        # cuántos "hops" de PCM hacen falta acumulados para tener una
        # ventana completa
        hops_per_window = -(-self.window_seconds // self.hop_seconds)  # ceil
        buffer: deque[np.ndarray] = deque(maxlen=hops_per_window)

        decode = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "warning", "-re",
                "-i", self.url,
                "-vn", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
        )
        stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(decode.stderr,), daemon=True
        )
        stderr_thread.start()
        try:
            while not self._stop_event.is_set():
                chunk = self._read_exact(decode.stdout, hop_bytes)
                if chunk is None:
                    logger.warning("ffmpeg dejó de dar datos (stdout cerrado)")
                    break
                pcm_hop = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                buffer.append(pcm_hop)

                if len(buffer) < hops_per_window:
                    continue  # todavía no hay suficiente audio para una ventana completa

                window_pcm = np.concatenate(list(buffer))[-window_samples:]
                window_start = datetime.datetime.utcnow() - datetime.timedelta(seconds=self.window_seconds)
                self._process_window(window_pcm, window_start)
        finally:
            decode.terminate()

    @staticmethod
    def _drain_stderr(stream) -> None:
        """Vuelca al log los avisos/errores de ffmpeg (reconexiones,
        cortes de red, etc.) — antes se descartaban con DEVNULL, lo que
        ocultaba la causa real de los cortes de stream."""
        for line in iter(stream.readline, b""):
            text = line.decode(errors="replace").strip()
            if text:
                logger.warning("ffmpeg: %s", text)

    @staticmethod
    def _read_exact(stream, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            piece = stream.read(n - len(buf))
            if not piece:
                return None
            buf.extend(piece)
        return bytes(buf)

    def _process_window(self, pcm: np.ndarray, window_start: datetime.datetime) -> None:
        try:
            seg_fp = fingerprint(pcm, SAMPLE_RATE)
        except Exception:
            logger.exception("error calculando huella")
            seg_fp = set()

        # WAV: el soporte de <audio> en el navegador para buscar/conocer la
        # duración de FLAC es poco fiable (se cortaba a los pocos segundos,
        # barra de progreso casi inmanejable). Con retención agresiva por
        # horas (ver cleanup.py) el tamaño ya no es problema.
        filename = f"{uuid.uuid4().hex}.wav"
        path: Path = SEGMENTS_DIR / filename
        sf.write(str(path), pcm, SAMPLE_RATE, format="WAV")

        insert_segmento(
            self.radio_key, window_start, self.window_seconds,
            fingerprint_to_bytes(seg_fp) if seg_fp else None, filename,
        )

        self.n_segmentos += 1
        self.last_segment_at = window_start

        self._windows_since_regroup += 1
        if self._windows_since_regroup >= REGROUP_EVERY_N_WINDOWS:
            self._windows_since_regroup = 0
            regroup_async(self.radio_key)

    def status(self) -> dict:
        return {
            "key": self.radio_key,
            "nombre": self.nombre,
            "connected": self.connected,
            "n_segmentos": self.n_segmentos,
            "started_at": self.started_at.isoformat(),
            "last_segment_at": self.last_segment_at.isoformat() if self.last_segment_at else None,
        }
