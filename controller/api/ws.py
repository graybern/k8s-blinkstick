import asyncio
import json
import logging
import time

from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)

MAX_CONNECTIONS = 10
MIN_INTERVAL = 0.1


class ConnectionManager:
    def __init__(self):
        self._connections: list[WebSocket] = []
        self._last_broadcast = 0.0

    async def connect(self, ws: WebSocket) -> bool:
        if len(self._connections) >= MAX_CONNECTIONS:
            await ws.close(code=1013, reason="max connections")
            return False
        await ws.accept()
        self._connections.append(ws)
        log.info("WebSocket connected (%d total)", len(self._connections))
        return True

    async def disconnect(self, ws: WebSocket):
        if ws in self._connections:
            self._connections.remove(ws)
        log.info("WebSocket disconnected (%d total)", len(self._connections))

    async def broadcast(self, data: dict):
        now = time.time()
        if now - self._last_broadcast < MIN_INTERVAL:
            return
        self._last_broadcast = now

        if not self._connections:
            return

        message = json.dumps(data)
        dead = []
        for ws in self._connections:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self._connections:
                self._connections.remove(ws)

    @property
    def count(self) -> int:
        return len(self._connections)
