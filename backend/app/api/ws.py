"""WebSocket para el estado en tiempo real de las emisoras.

Los workers corren en hilos aparte y notifican cambios de estado de forma
síncrona; `ConnectionManager.broadcast_threadsafe` los reenvía al loop de
asyncio de uvicorn para difundirlos a los clientes conectados.
"""

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def _broadcast(self, message: dict) -> None:
        dead = []
        payload = json.dumps(message)
        for ws in self.active:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)

    def broadcast_threadsafe(self, message: dict) -> None:
        """Llamable desde cualquier hilo (workers)."""
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(message), self.loop)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # no esperamos mensajes del cliente, solo mantenemos viva la conexión
    except WebSocketDisconnect:
        manager.disconnect(ws)
