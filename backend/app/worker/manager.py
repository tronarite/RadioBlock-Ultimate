"""Gestiona el ciclo de vida de los workers, uno por radio activa.

Asigna puertos de proxy dinámicamente (8001, 8002, ...) y expone el
estado en vivo de cada radio para el dashboard / websocket.
"""

import threading

from app.config import PROXY_PORT_BASE, PROXY_PORT_MAX
from app.db.models import Radio
from app.worker.tunnel import CloudflareTunnel
from app.worker.worker import RadioWorker


class WorkerManager:
    def __init__(self, session_factory, on_state_change=None):
        self.session_factory = session_factory
        self.on_state_change = on_state_change or (lambda *_: None)
        self._workers: dict[int, RadioWorker] = {}
        self._used_ports: set[int] = set()
        self._tunnels: dict[int, CloudflareTunnel] = {}
        self._lock = threading.Lock()

    def _allocate_port(self) -> int:
        for port in range(PROXY_PORT_BASE, PROXY_PORT_MAX + 1):
            if port not in self._used_ports:
                self._used_ports.add(port)
                return port
        raise RuntimeError("no free proxy ports available")

    def start_radio(self, radio: Radio) -> RadioWorker:
        with self._lock:
            if radio.id in self._workers:
                return self._workers[radio.id]
            port = self._allocate_port()
            worker = RadioWorker(
                radio_id=radio.id,
                nombre=radio.nombre,
                url=radio.url,
                port=port,
                segment_duration=radio.segment_duration_seconds,
                confidence_threshold=radio.confidence_threshold,
                session_factory=self.session_factory,
                on_state_change=self.on_state_change,
            )
            worker.start()
            self._workers[radio.id] = worker
            return worker

    def stop_radio(self, radio_id: int) -> None:
        with self._lock:
            worker = self._workers.pop(radio_id, None)
            if worker is None:
                return
            self._used_ports.discard(worker.port)
        worker.stop()
        self.stop_tunnel(radio_id)

    def get_worker(self, radio_id: int) -> RadioWorker | None:
        return self._workers.get(radio_id)

    def start_tunnel(self, radio_id: int) -> CloudflareTunnel | None:
        worker = self._workers.get(radio_id)
        if worker is None:
            return None
        existing = self._tunnels.get(radio_id)
        if existing is not None and existing.state in ("arrancando", "activo"):
            return existing
        tunnel = CloudflareTunnel(worker.port)
        tunnel.start()
        self._tunnels[radio_id] = tunnel
        return tunnel

    def stop_tunnel(self, radio_id: int) -> None:
        tunnel = self._tunnels.pop(radio_id, None)
        if tunnel is not None:
            tunnel.stop()

    def tunnel_status(self, radio_id: int) -> dict | None:
        tunnel = self._tunnels.get(radio_id)
        if tunnel is None:
            return None
        return {"state": tunnel.state, "url": tunnel.url}

    def mark_recent(self, radio_id: int, label: str) -> list[int] | None:
        worker = self._workers.get(radio_id)
        if worker is None:
            return None
        return worker.mark_recent(label)

    def start_marking(self, radio_id: int, label: str) -> list[int] | None:
        worker = self._workers.get(radio_id)
        if worker is None:
            return None
        return worker.start_marking(label)

    def stop_marking(self, radio_id: int) -> bool:
        worker = self._workers.get(radio_id)
        if worker is None:
            return False
        worker.stop_marking()
        return True

    def status(self, radio_id: int) -> dict | None:
        worker = self._workers.get(radio_id)
        return worker._status_dict() if worker else None

    def all_statuses(self) -> dict[int, dict]:
        return {rid: w._status_dict() for rid, w in self._workers.items()}

    def refresh_config(self, radio: Radio) -> None:
        """Aplica cambios de umbral/duración de segmento sin reiniciar el worker."""
        worker = self._workers.get(radio.id)
        if worker:
            worker.confidence_threshold = radio.confidence_threshold
            worker.segment_duration = radio.segment_duration_seconds
