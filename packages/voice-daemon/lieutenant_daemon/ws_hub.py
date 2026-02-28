"""WebSocket hub — broadcasts events to all connected UI clients."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("lieutenant-daemon")

# ── Ring-buffer for recent log lines (so new WS clients get context) ──
_MAX_LOG_HISTORY = 200


class WSHub:
    """Manages WebSocket connections and broadcasting."""

    def __init__(self):
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._log_history: deque[dict] = deque(maxlen=_MAX_LOG_HISTORY)

    async def connect(self, ws: WebSocket):
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
        logger.info("WS client connected (%d total)", len(self._clients))

    async def disconnect(self, ws: WebSocket):
        async with self._lock:
            self._clients.discard(ws)
        logger.info("WS client disconnected (%d total)", len(self._clients))

    async def broadcast(self, msg: dict[str, Any]):
        """Send a JSON message to all connected clients."""
        msg.setdefault("ts", time.time())
        payload = json.dumps(msg, ensure_ascii=False)
        async with self._lock:
            dead: list[WebSocket] = []
            for ws in self._clients:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)

    # ── Typed senders ─────────────────────────────────────────────────
    async def send_state(self, state_value: str):
        await self.broadcast({"type": "state", "value": state_value})

    async def send_mic_level(self, rms: float):
        await self.broadcast({"type": "mic.level", "rms": round(rms, 4)})

    async def send_stt_partial(self, text: str):
        await self.broadcast({"type": "stt.partial", "text": text})

    async def send_stt_final(self, text: str):
        await self.broadcast({"type": "stt.final", "text": text})

    async def send_agent_chunk(self, text: str):
        await self.broadcast({"type": "agent.chunk", "text": text})

    async def send_agent_done(self):
        await self.broadcast({"type": "agent.done"})

    async def send_llm_backend(self, name: str):
        await self.broadcast({"type": "llm.backend", "name": name})

    async def send_tts_level(self, rms: float):
        await self.broadcast({"type": "tts.level", "rms": round(rms, 4)})

    async def send_error(self, message: str):
        await self.broadcast({"type": "error", "message": message})

    async def send_log(self, level: str, message: str, source: str = "daemon"):
        """Broadcast a log line to UI and stash in history."""
        entry = {"type": "log", "level": level, "message": message, "source": source, "ts": time.time()}
        self._log_history.append(entry)
        await self.broadcast(entry)

    async def send_log_history(self, ws: WebSocket):
        """Send buffered log history to a newly connected client."""
        for entry in self._log_history:
            try:
                await ws.send_text(json.dumps(entry, ensure_ascii=False))
            except Exception:
                break

    @property
    def client_count(self) -> int:
        return len(self._clients)
