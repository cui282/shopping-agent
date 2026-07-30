from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """One live socket per thread with ordered event history replay."""

    def __init__(self, max_events: int = 512, send_timeout_seconds: float = 1.0) -> None:
        self.active: dict[str, WebSocket] = {}
        self._send_timeout_seconds = send_timeout_seconds
        self._events: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_events)
        )
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def connect(self, thread_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._locks[thread_id]:
            previous = self.active.get(thread_id)
            if previous is not None and previous is not websocket:
                try:
                    await asyncio.wait_for(
                        previous.close(code=1000, reason="replaced"),
                        timeout=self._send_timeout_seconds,
                    )
                except (asyncio.TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
                    pass
            self.active[thread_id] = websocket
            try:
                await asyncio.wait_for(
                    self._replay(websocket, list(self._events[thread_id])),
                    timeout=self._send_timeout_seconds,
                )
            except (asyncio.TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
                if self.active.get(thread_id) is websocket:
                    self.active.pop(thread_id, None)
                try:
                    await asyncio.wait_for(
                        websocket.close(code=1011, reason="event replay failed"),
                        timeout=self._send_timeout_seconds,
                    )
                except (asyncio.TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
                    pass

    @staticmethod
    async def _replay(websocket: WebSocket, events: list[dict[str, Any]]) -> None:
        for event in events:
            await websocket.send_json(event)

    async def disconnect(self, thread_id: str, websocket: WebSocket) -> None:
        async with self._locks[thread_id]:
            if self.active.get(thread_id) is websocket:
                self.active.pop(thread_id, None)

    async def send_to_thread(self, thread_id: str, payload: dict[str, Any]) -> None:
        async with self._locks[thread_id]:
            self._events[thread_id].append(payload)
            websocket = self.active.get(thread_id)
            if websocket is None:
                return
            try:
                await asyncio.wait_for(
                    websocket.send_json(payload), timeout=self._send_timeout_seconds
                )
            except (asyncio.TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
                if self.active.get(thread_id) is websocket:
                    self.active.pop(thread_id, None)

    async def send_ephemeral(self, thread_id: str, payload: dict[str, Any]) -> None:
        async with self._locks[thread_id]:
            websocket = self.active.get(thread_id)
            if websocket is not None:
                await websocket.send_json(payload)

    def history(self, thread_id: str) -> list[dict[str, Any]]:
        return list(self._events.get(thread_id, ()))

    async def clear(self, thread_id: str) -> None:
        async with self._locks[thread_id]:
            self._events.pop(thread_id, None)


manager = ConnectionManager()
