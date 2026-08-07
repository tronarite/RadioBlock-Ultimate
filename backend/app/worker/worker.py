"""Worker de una emisora: lee el stream, lo segmenta, lo analiza, y
reemite (silenciando anuncios) a través del proxy de audio.

Un `RadioWorker` corre en su propio hilo. Internamente usa dos procesos
`ffmpeg`:
  - decode: emisora -> PCM crudo (para poder analizar y, si hace falta,
    poner a cero las muestras de un segmento).
  - encode: PCM (con o sin silencio) -> MP3, que se retransmite a los
    clientes conectados al proxy.
"""

from __future__ import annotations

import datetime
import subprocess
import threading
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

from app.analysis import fingerprint as fp_module
from app.analysis import model as model_module
from app.config import RETRAIN_EVERY_N_SEGMENTS, SAMPLE_RATE, SEGMENTS_DIR
from app.db.models import Cluster, Muteo, Segmento
from app.worker.proxy import AudioBroadcaster, ProxyServer

BYTES_PER_SAMPLE = 2  # s16le mono
MAX_BACKOFF_SECONDS = 60


class RadioWorker:
    def __init__(
        self,
        radio_id: int,
        nombre: str,
        url: str,
        port: int,
        segment_duration: int,
        confidence_threshold: float,
        session_factory,
        on_state_change=None,
    ):
        self.radio_id = radio_id
        self.nombre = nombre
        self.url = url
        self.port = port
        self.segment_duration = segment_duration
        self.confidence_threshold = confidence_threshold
        self.session_factory = session_factory
        self.on_state_change = on_state_change or (lambda *_: None)

        self.broadcaster = AudioBroadcaster()
        self.proxy = ProxyServer(port, self.broadcaster)

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self.state = "caido"  # musica | anuncio | silencio | caido
        self.connected = False
        self.pending_count = 0  # nº de patrones repetidos detectados y aún sin revisar
        self.last_level = 0.0  # RMS del último segmento analizado, para la gráfica en vivo
        self._active_muteo: Muteo | None = None
        self._muteo_lock = threading.Lock()
        self._segments_since_retrain = 0

        # Marcado manual en directo ("estoy oyendo un anuncio ahora mismo"
        # desde el panel): al aplicarse, etiqueta los segmentos recientes ya
        # persistidos (cubre el retraso del proxy/reproductor) y arma que el
        # próximo segmento que se cierre (el que se está capturando en este
        # instante) también reciba la misma etiqueta.
        self._pending_manual_label: str | None = None
        self._pending_manual_remaining = 0
        self._manual_lock = threading.Lock()

    # -- ciclo de vida -----------------------------------------------------

    def start(self) -> None:
        self.proxy.start()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.proxy.stop()

    # -- bucle principal -----------------------------------------------------

    def _run_loop(self) -> None:
        backoff = 1
        while not self._stop_event.is_set():
            try:
                self._set_state("caido", connected=False)
                self._stream_once()
                backoff = 1  # conexión terminó limpiamente: reintenta rápido
            except Exception:
                pass
            if self._stop_event.is_set():
                break
            self._set_state("caido", connected=False)
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    def _stream_once(self) -> None:
        segment_bytes = self.segment_duration * SAMPLE_RATE * BYTES_PER_SAMPLE

        decode = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error", "-re",
                "-i", self.url,
                "-vn", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        encode = subprocess.Popen(
            [
                "ffmpeg", "-loglevel", "error",
                "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
                "-i", "pipe:0",
                "-f", "mp3", "-b:a", "128k",
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        broadcast_thread = threading.Thread(
            target=self._pump_encoded_output, args=(encode,), daemon=True
        )
        broadcast_thread.start()

        self._set_state(self.state, connected=True)

        try:
            while not self._stop_event.is_set():
                chunk = self._read_exact(decode.stdout, segment_bytes)
                if chunk is None:
                    break
                self._process_segment(chunk, encode)
        finally:
            for proc in (decode, encode):
                if proc.stdin:
                    try:
                        proc.stdin.close()
                    except OSError:
                        pass
                proc.terminate()
            self.broadcaster.broadcast(None)  # despierta a los clientes para que cierren
            broadcast_thread.join(timeout=2)

    @staticmethod
    def _read_exact(stream, n: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < n:
            piece = stream.read(n - len(buf))
            if not piece:
                return None
            buf.extend(piece)
        return bytes(buf)

    def _pump_encoded_output(self, encode: subprocess.Popen) -> None:
        while True:
            chunk = encode.stdout.read(4096)
            if not chunk:
                break
            self.broadcaster.broadcast(chunk)

    # -- análisis por segmento -----------------------------------------------------

    def _process_segment(self, pcm_bytes: bytes, encode: subprocess.Popen) -> None:
        pcm = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        self.last_level = float(np.sqrt(np.mean(np.square(pcm)))) if pcm.size else 0.0

        try:
            seg_fp = fp_module.fingerprint(pcm, SAMPLE_RATE)
            label, confidence = model_module.predict(
                self.radio_id, seg_fp, self.confidence_threshold
            )
        except Exception:
            seg_fp, label, confidence = set(), "desconocido", 0.0

        muted = label == "anuncio"
        out_bytes = b"\x00" * len(pcm_bytes) if muted else pcm_bytes

        self._set_state("anuncio" if muted else "musica", connected=True)
        self._update_muteo(muted)

        self._persist_segment(pcm, seg_fp, label, confidence)

        if encode.stdin:
            try:
                encode.stdin.write(out_bytes)
            except (BrokenPipeError, OSError):
                pass

    def _persist_segment(
        self, pcm: np.ndarray, seg_fp: set[int], label: str, confidence: float
    ) -> None:
        with self._manual_lock:
            manual_label = None
            if self._pending_manual_remaining > 0 and self._pending_manual_label:
                manual_label = self._pending_manual_label
                self._pending_manual_remaining -= 1
                if self._pending_manual_remaining <= 0:
                    self._pending_manual_label = None

        if manual_label:
            label, confidence = manual_label, 1.0

        db = self.session_factory()
        try:
            archivo_audio = None
            if label == "desconocido" or manual_label:
                archivo_audio = self._save_sample(pcm)

            seg = Segmento(
                radio_id=self.radio_id,
                timestamp=datetime.datetime.utcnow(),
                duracion=self.segment_duration,
                fingerprint=fp_module.fingerprint_to_bytes(seg_fp) if seg_fp else None,
                label=label,
                confidence=confidence,
                label_usuario=manual_label,
                archivo_audio=archivo_audio,
            )
            db.add(seg)
            db.commit()

            # "Pendientes" son patrones repetidos detectados por el clustering no
            # supervisado y aún sin revisar — no cada segmento desconocido suelto
            # (una tertulia genera habla que nunca se repite y no debe molestar).
            self.pending_count = (
                db.query(Cluster)
                .filter(Cluster.radio_id == self.radio_id, Cluster.label.is_(None))
                .count()
            )
            self.on_state_change(self.radio_id, self._status_dict())
        finally:
            db.close()

        self._segments_since_retrain += 1
        if manual_label or self._segments_since_retrain >= RETRAIN_EVERY_N_SEGMENTS:
            self._segments_since_retrain = 0
            model_module.retrain_async(self.radio_id, self.session_factory)

    def mark_recent(self, label: str) -> list[int]:
        """Marcado manual en directo: el usuario está oyendo el proxy en el
        panel y pulsa "es un anuncio" (o "no lo es") justo cuando suena.
        Etiqueta los segmentos ya persistidos dentro de un margen — para
        cubrir el retraso de buffering del proxy/reproductor — y arma que
        el siguiente segmento que se cierre (el que se está capturando en
        este instante) reciba la misma etiqueta en cuanto se persista."""
        with self._manual_lock:
            self._pending_manual_label = label
            self._pending_manual_remaining = 1

        db = self.session_factory()
        try:
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(
                seconds=self.segment_duration * 2
            )
            segs = (
                db.query(Segmento)
                .filter(Segmento.radio_id == self.radio_id, Segmento.timestamp >= cutoff)
                .all()
            )
            marcados = []
            for s in segs:
                s.label = label
                s.label_usuario = label
                s.confidence = 1.0
                marcados.append(s.id)
            db.commit()
        finally:
            db.close()

        model_module.retrain_async(self.radio_id, self.session_factory)
        return marcados

    def _save_sample(self, pcm: np.ndarray) -> str:
        filename = f"{self.radio_id}_{uuid.uuid4().hex}.wav"
        path: Path = SEGMENTS_DIR / filename
        sf.write(str(path), pcm, SAMPLE_RATE)
        return filename

    def _update_muteo(self, muted: bool) -> None:
        db = self.session_factory()
        try:
            with self._muteo_lock:
                now = datetime.datetime.utcnow()
                if muted and self._active_muteo is None:
                    m = Muteo(radio_id=self.radio_id, timestamp_inicio=now, duracion=0.0)
                    db.add(m)
                    db.commit()
                    self._active_muteo = m
                elif muted and self._active_muteo is not None:
                    self._active_muteo.duracion += self.segment_duration
                    self._active_muteo.timestamp_fin = now
                    db.merge(self._active_muteo)
                    db.commit()
                elif not muted and self._active_muteo is not None:
                    self._active_muteo = None
        finally:
            db.close()

    # -- estado / estadísticas en vivo -----------------------------------------------------

    def _set_state(self, state: str, connected: bool) -> None:
        changed = state != self.state or connected != self.connected
        self.state = state
        self.connected = connected
        if changed:
            self.on_state_change(self.radio_id, self._status_dict())

    def _status_dict(self) -> dict:
        return {
            "radio_id": self.radio_id,
            "nombre": self.nombre,
            "state": self.state,
            "connected": self.connected,
            "pending_count": self.pending_count,
            "proxy_port": self.port,
            "n_clients": self.broadcaster.n_clients,
            "level": self.last_level,
        }
