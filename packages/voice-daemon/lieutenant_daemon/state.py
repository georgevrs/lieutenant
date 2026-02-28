"""State machine for the voice daemon."""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Callable, Awaitable

logger = logging.getLogger("lieutenant-daemon")


class State(str, enum.Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    CONVERSING = "CONVERSING"  # Follow-up listening — no wake word needed


class StateMachine:
    """Manages state transitions and notifies listeners."""

    def __init__(self):
        self._state = State.IDLE
        self._listeners: list[Callable[[State], Awaitable[None]]] = []
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        return self._state

    def on_change(self, callback: Callable[[State], Awaitable[None]]):
        self._listeners.append(callback)

    async def transition(self, new_state: State):
        async with self._lock:
            old = self._state
            if old == new_state:
                return
            self._state = new_state
            logger.info("State: %s -> %s", old.value, new_state.value)
            for cb in self._listeners:
                try:
                    await cb(new_state)
                except Exception as e:
                    logger.error("State listener error: %s", e)

    async def reset(self):
        await self.transition(State.IDLE)
