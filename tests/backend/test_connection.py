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


class GatedAcceptWebSocket(FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.accept_started = asyncio.Event()
        self.release_accept = asyncio.Event()

    async def accept(self) -> None:
        self.accept_started.set()
        await self.release_accept.wait()
        self.accepted = True


class GatedFirstSendWebSocket(FakeWebSocket):
    def __init__(self) -> None:
        super().__init__()
        self.first_send_started = asyncio.Event()
        self.release_first_send = asyncio.Event()

    async def send_json(self, payload: dict[str, Any]) -> None:
        if not self.sent:
            self.first_send_started.set()
            await self.release_first_send.wait()
        self.sent.append(payload)


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


@pytest.mark.asyncio
async def test_discard_closes_socket_and_clears_buffered_events() -> None:
    connections = ConnectionManager()
    websocket = FakeWebSocket()
    await connections.connect("thread-a", websocket)  # type: ignore[arg-type]
    await connections.send_to_thread("thread-a", {"event": "session_created"})

    await connections.discard("thread-a")

    assert websocket.closed
    assert "thread-a" not in connections.active
    assert connections.history("thread-a") == []


@pytest.mark.asyncio
async def test_discard_invalidates_connection_that_is_still_being_accepted() -> None:
    connections = ConnectionManager()
    websocket = GatedAcceptWebSocket()
    connecting = asyncio.create_task(
        connections.connect("thread-a", websocket)  # type: ignore[arg-type]
    )
    await websocket.accept_started.wait()

    await connections.discard("thread-a")
    websocket.release_accept.set()

    assert await connecting is False
    assert websocket.closed
    assert "thread-a" not in connections.active


@pytest.mark.asyncio
async def test_durable_bootstrap_precedes_a_concurrent_live_event_without_truncation() -> None:
    connections = ConnectionManager(max_events=1)
    first = {"type": "monitor_event", "event_id": "evt-1", "sequence": 1}
    second = {"type": "monitor_event", "event_id": "evt-2", "sequence": 2}
    live = {"type": "monitor_event", "event_id": "evt-3", "sequence": 3}
    snapshot = {
        "type": "task_snapshot",
        "snapshot": {"events": [first, second]},
    }
    websocket = GatedFirstSendWebSocket()

    connecting = asyncio.create_task(
        connections.connect(  # type: ignore[arg-type]
            "thread-a",
            websocket,
            bootstrap=lambda: snapshot,
        )
    )
    await websocket.first_send_started.wait()
    publishing = asyncio.create_task(connections.send_to_thread("thread-a", live))
    assert not publishing.done()

    websocket.release_first_send.set()
    assert await connecting is True
    await publishing

    assert websocket.sent == [snapshot, first, second, live]
