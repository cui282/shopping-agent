from __future__ import annotations

import asyncio
import hashlib
import threading
from dataclasses import dataclass

from app.config import get_settings


class RuntimeNotAccepting(RuntimeError):
    """Raised when a release is draining, rolled back, or not selected for a request."""

    def __init__(self, message: str, *, code: str = "runtime_draining") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    release_channel: str
    release_id: str
    traffic_percent: int
    rollback: bool
    draining: bool
    active_tasks: int


class RuntimeControl:
    """Release gate and graceful-drain state for one application worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._draining = False
        self._active_tasks = 0
        self._idle_events: dict[int, asyncio.Event] = {}

    def _event(self) -> asyncio.Event:
        loop_id = id(asyncio.get_running_loop())
        event = self._idle_events.get(loop_id)
        if event is None:
            event = asyncio.Event()
            self._idle_events[loop_id] = event
        if self._active_tasks == 0:
            event.set()
        else:
            event.clear()
        return event

    def reset_for_startup(self) -> None:
        with self._lock:
            self._draining = False

    def begin_drain(self) -> None:
        with self._lock:
            self._draining = True

    def _release_snapshot_unlocked(self) -> RuntimeSnapshot:
        settings = get_settings()
        return RuntimeSnapshot(
            release_channel=settings.release_channel,
            release_id=settings.release_id,
            traffic_percent=settings.release_traffic_percent,
            rollback=settings.release_rollback,
            draining=self._draining,
            active_tasks=self._active_tasks,
        )

    def _release_snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._release_snapshot_unlocked()

    def snapshot(self) -> RuntimeSnapshot:
        return self._release_snapshot()

    def selected_for(self, request_id: str) -> bool:
        snapshot = self._release_snapshot()
        if snapshot.rollback or snapshot.draining:
            return False
        if snapshot.release_channel == "stable" or snapshot.traffic_percent >= 100:
            return True
        digest = hashlib.sha256(request_id.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % 100
        return bucket < snapshot.traffic_percent

    def begin_task(self, request_id: str) -> None:
        with self._lock:
            snapshot = self._release_snapshot_unlocked()
            if snapshot.rollback:
                raise RuntimeNotAccepting("release is marked for rollback")
            if self._draining:
                raise RuntimeNotAccepting("worker is draining")
            if snapshot.release_channel == "canary" and snapshot.traffic_percent < 100:
                digest = hashlib.sha256(request_id.encode("utf-8")).digest()
                bucket = int.from_bytes(digest[:4], "big") % 100
                if bucket >= snapshot.traffic_percent:
                    raise RuntimeNotAccepting(
                        "request is outside canary traffic", code="release_not_selected"
                    )
            self._active_tasks += 1
            for event in self._idle_events.values():
                event.clear()

    def finish_task(self) -> None:
        with self._lock:
            self._active_tasks = max(0, self._active_tasks - 1)
            if self._active_tasks == 0:
                for event in self._idle_events.values():
                    event.set()

    async def wait_for_idle(self, timeout: float) -> bool:
        event = self._event()
        if self._active_tasks == 0:
            return True
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True


_runtime = RuntimeControl()


def get_runtime_control() -> RuntimeControl:
    return _runtime


__all__ = ["RuntimeControl", "RuntimeNotAccepting", "RuntimeSnapshot", "get_runtime_control"]
