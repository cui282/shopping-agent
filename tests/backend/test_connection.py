from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.api.connection import ConnectionManager


class FakeWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, **_: Any) -> None:
        self.closed = True


class HangingWebSocket(FakeWebSocket):
    async def send_json(self, payload: dict[str, Any]) -> None:
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_events_are_buffered_replayed_and_continue_in_order() -> None:
    connections = ConnectionManager()
    first = {"event": "session_created"}
    second = {"event": "assistant_call"}
    third = {"event": "tool_start"}
    await connections.send_to_thread("thread-a", first)
    await connections.send_to_thread("thread-a", second)

    websocket = FakeWebSocket()
    await connections.connect("thread-a", websocket)  # type: ignore[arg-type]
    await connections.send_to_thread("thread-a", third)

    assert websocket.accepted
    assert websocket.sent == [first, second, third]
    await connections.disconnect("thread-a", websocket)  # type: ignore[arg-type]
    assert "thread-a" not in connections.active


@pytest.mark.asyncio
async def test_replacement_socket_does_not_get_removed_by_stale_disconnect() -> None:
    connections = ConnectionManager()
    old_socket = FakeWebSocket()
    new_socket = FakeWebSocket()
    await connections.connect("thread-a", old_socket)  # type: ignore[arg-type]
    await connections.connect("thread-a", new_socket)  # type: ignore[arg-type]
    await connections.disconnect("thread-a", old_socket)  # type: ignore[arg-type]
    assert connections.active["thread-a"] is new_socket
    assert old_socket.closed


@pytest.mark.asyncio
async def test_slow_broadcast_times_out_without_losing_event() -> None:
    connections = ConnectionManager(send_timeout_seconds=0.01)
    websocket = HangingWebSocket()
    connections.active["thread-a"] = websocket  # type: ignore[assignment]
    event = {"event": "task_result"}

    await asyncio.wait_for(connections.send_to_thread("thread-a", event), timeout=0.2)

    assert connections.history("thread-a") == [event]
    assert "thread-a" not in connections.active


@pytest.mark.asyncio
async def test_slow_replay_releases_lock_and_preserves_history() -> None:
    connections = ConnectionManager(send_timeout_seconds=0.01)
    first = {"event": "session_created"}
    second = {"event": "assistant_call"}
    await connections.send_to_thread("thread-a", first)

    websocket = HangingWebSocket()
    await asyncio.wait_for(
        connections.connect("thread-a", websocket),  # type: ignore[arg-type]
        timeout=0.2,
    )
    await asyncio.wait_for(connections.send_to_thread("thread-a", second), timeout=0.2)

    assert websocket.closed
    assert "thread-a" not in connections.active
    assert connections.history("thread-a") == [first, second]
