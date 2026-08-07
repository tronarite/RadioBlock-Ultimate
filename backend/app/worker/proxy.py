"""Proxy HTTP de audio: sirve a los clientes (VLC, navegador, ...) el stream
reencodado, con los segmentos de anuncio sustituidos por silencio.

Cada radio activa tiene un `AudioBroadcaster` (cola por cliente conectado) y
un `ProxyServer` HTTP en un puerto propio (8001, 8002, ...).
"""

import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class AudioBroadcaster:
    """Reparte los bytes de audio ya codificados a todos los clientes conectados."""

    def __init__(self) -> None:
        self._clients: set[queue.Queue] = set()
        self._lock = threading.Lock()

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q: queue.Queue) -> None:
        with self._lock:
            self._clients.discard(q)

    def broadcast(self, chunk: bytes) -> None:
        with self._lock:
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(chunk)
            except queue.Full:
                # Cliente lento: descartamos el chunk más viejo para no bloquear el resto.
                try:
                    q.get_nowait()
                    q.put_nowait(chunk)
                except queue.Empty:
                    pass

    @property
    def n_clients(self) -> int:
        with self._lock:
            return len(self._clients)


def make_handler(broadcaster: AudioBroadcaster, content_type: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # silencia el logging por defecto de http.server

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            q = broadcaster.register()
            try:
                while True:
                    chunk = q.get()
                    if chunk is None:  # señal de cierre del worker
                        break
                    self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                broadcaster.unregister(q)

    return Handler


class ProxyServer:
    """Servidor HTTP de un único endpoint que retransmite el audio en directo."""

    def __init__(self, port: int, broadcaster: AudioBroadcaster, content_type: str = "audio/mpeg"):
        self.port = port
        self.broadcaster = broadcaster
        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), make_handler(broadcaster, content_type))
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
