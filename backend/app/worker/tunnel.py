"""Túnel público de Cloudflare (Quick Tunnel) para exponer el proxy de una
radio a internet, sin necesitar cuenta ni dominio propio de Cloudflare:
lanza `cloudflared tunnel --url http://localhost:<puerto>` y captura la
URL `https://xxxx.trycloudflare.com` que Cloudflare asigna.

Es una URL efímera — cambia cada vez que se relanza el túnel, y Cloudflare
no da garantía de uptime para este modo "quick tunnel" (pensado para
pruebas, no producción). Es la opción con menos fricción posible: no pide
login ni dominio propio, solo tener `cloudflared` instalado.
"""

from __future__ import annotations

import re
import subprocess
import threading

URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


class CloudflareTunnel:
    def __init__(self, local_port: int):
        self.local_port = local_port
        self.url: str | None = None
        self.state = "arrancando"  # arrancando | activo | error | apagado
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        try:
            self._proc = subprocess.Popen(
                ["cloudflared", "tunnel", "--url", f"http://localhost:{self.local_port}", "--no-autoupdate"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            self.state = "error"
            self._ready.set()
            return
        self._thread = threading.Thread(target=self._read_output, daemon=True)
        self._thread.start()

    def _read_output(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            if self.url is None:
                m = URL_RE.search(line)
                if m:
                    self.url = m.group(0)
                    self.state = "activo"
                    self._ready.set()
        # El proceso ha terminado (cloudflared falló, o se llamó a stop()).
        if self.url is None:
            self.state = "error"
        self._ready.set()

    def wait_ready(self, timeout: float = 25.0) -> bool:
        return self._ready.wait(timeout)

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self.state = "apagado"
        self.url = None
