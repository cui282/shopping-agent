"""Bounded dual-queue scheduling for normal and long-running research tasks.

The queue is intentionally process-local, like the task registry. A durable queue belongs at
the gateway/worker layer; this primitive keeps one API worker from letting long requests starve
short ones and gives the WebSocket layer a truthful position estimate while a task waits.
"""

from __future__ import annotations

import asyncio
import heapq
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal, TypeVar

QueueType = Literal["normal", "heavy"]
T = TypeVar("T")

USER_PRIORITY: dict[str, int] = {
    "premium": 1,
    "standard": 5,
    "free": 10,
}


def priority_for_user_tier(user_tier: str | None) -> int:
    """Return a bounded priority without trusting arbitrary client input."""

    return USER_PRIORITY.get((user_tier or "standard").strip().lower(), USER_PRIORITY["standard"])


@dataclass(order=True, slots=True)
class PrioritizedRequest:
    """A queue item where lower priority values are served first."""

    priority: int
    timestamp: float = field(compare=False)
    thread_id: str = field(compare=False)
    query: str = field(compare=False)
    user_id: str | None = field(compare=False, default=None)


class PriorityRequestQueue:
    """Dual priority queues with cancellation-safe worker leases.

    Normal requests use their own worker budget. Heavy requests use a smaller pool and are
    throttled further while the normal queue is severely backed up. The queue never replaces a
    semaphore with live waiters; capacity changes are represented by counters and a condition,
    which makes rebalance safe under cancellation.
    """

    def __init__(
        self,
        normal_workers: int = 8,
        heavy_workers: int = 4,
        *,
        heavy_threshold: int = 30,
        rebalance_threshold: int = 20,
        average_task_seconds: float = 10.0,
    ) -> None:
        if normal_workers < 1 or heavy_workers < 1:
            raise ValueError("worker counts must be positive")
        self.normal_workers = normal_workers
        self.heavy_workers = heavy_workers
        self.heavy_threshold = max(1, heavy_threshold)
        self.rebalance_threshold = max(1, rebalance_threshold)
        self.average_task_seconds = max(0.1, average_task_seconds)
        self._normal_queue: list[PrioritizedRequest] = []
        self._heavy_queue: list[PrioritizedRequest] = []
        self._normal_active = 0
        self._heavy_active = 0
        self._heavy_limit = heavy_workers
        self._condition = asyncio.Condition()

    def classify(self, dialog_turns: int) -> QueueType:
        """Route conversations with 30 or more turns to the heavy pool."""

        return "heavy" if dialog_turns >= self.heavy_threshold else "normal"

    @staticmethod
    def _queue_type(queue_type: str) -> QueueType:
        return "heavy" if queue_type == "heavy" else "normal"

    def _queue(self, queue_type: QueueType) -> list[PrioritizedRequest]:
        return self._heavy_queue if queue_type == "heavy" else self._normal_queue

    def _active(self, queue_type: QueueType) -> int:
        return self._heavy_active if queue_type == "heavy" else self._normal_active

    def _capacity(self, queue_type: QueueType) -> int:
        return self._heavy_limit if queue_type == "heavy" else self.normal_workers

    def _set_active(self, queue_type: QueueType, value: int) -> None:
        if queue_type == "heavy":
            self._heavy_active = value
        else:
            self._normal_active = value

    def _rebalance_locked(self) -> None:
        # When normal work is piling up, reserve more capacity for it by reducing new heavy
        # admissions. Existing heavy tasks are allowed to finish.
        if len(self._normal_queue) > self.rebalance_threshold:
            self._heavy_limit = max(1, self.heavy_workers // 2)
        elif len(self._normal_queue) < self.rebalance_threshold // 4 + 1:
            self._heavy_limit = self.heavy_workers

    def _position_locked(self, request: PrioritizedRequest, queue_type: QueueType) -> int:
        queue = self._queue(queue_type)
        ordered = sorted(queue)
        for index, item in enumerate(ordered):
            if item is request:
                return index + 1
        return 0

    def position(self, request: PrioritizedRequest, queue_type: str) -> int:
        """Return a best-effort 1-based position (zero once the request is running)."""

        return self._position_locked(request, self._queue_type(queue_type))

    def estimate_wait_seconds(self, request: PrioritizedRequest, queue_type: str) -> float:
        queue_kind = self._queue_type(queue_type)
        position = self.position(request, queue_kind)
        capacity = max(1, self._capacity(queue_kind))
        return round(max(0, position - 1) * self.average_task_seconds / capacity, 1)

    @property
    def pending(self) -> int:
        return len(self._normal_queue) + len(self._heavy_queue)

    @property
    def active(self) -> int:
        return self._normal_active + self._heavy_active

    async def enqueue(self, request: PrioritizedRequest, queue_type: str) -> int:
        """Add a request and return its current queue position."""

        queue_kind = self._queue_type(queue_type)
        async with self._condition:
            heapq.heappush(self._queue(queue_kind), request)
            self._rebalance_locked()
            position = self._position_locked(request, queue_kind)
            self._condition.notify_all()
            return position

    async def _remove(self, request: PrioritizedRequest, queue_type: QueueType) -> None:
        async with self._condition:
            queue = self._queue(queue_type)
            try:
                queue.remove(request)
            except ValueError:
                return
            heapq.heapify(queue)
            self._rebalance_locked()
            self._condition.notify_all()

    async def discard(self, request: PrioritizedRequest, queue_type: str) -> None:
        """Remove a request if it was cancelled before acquiring a worker."""

        await self._remove(request, self._queue_type(queue_type))

    @asynccontextmanager
    async def acquire(self, request: PrioritizedRequest, queue_type: str) -> AsyncIterator[None]:
        """Wait until this request reaches the head and a class worker is available."""

        queue_kind = self._queue_type(queue_type)
        try:
            async with self._condition:
                while True:
                    queue = self._queue(queue_kind)
                    if (
                        queue
                        and queue[0] is request
                        and self._active(queue_kind) < self._capacity(queue_kind)
                    ):
                        heapq.heappop(queue)
                        self._set_active(queue_kind, self._active(queue_kind) + 1)
                        self._rebalance_locked()
                        break
                    await self._condition.wait()
        except asyncio.CancelledError:
            await self._remove(request, queue_kind)
            raise

        try:
            yield
        finally:
            async with self._condition:
                self._set_active(queue_kind, max(0, self._active(queue_kind) - 1))
                self._rebalance_locked()
                self._condition.notify_all()

    async def run(
        self,
        request: PrioritizedRequest,
        queue_type: str,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Run an operation after admission, creating its coroutine only when admitted."""

        await self.enqueue(request, queue_type)
        async with self.acquire(request, queue_type):
            return await operation()

    async def dynamic_rebalance(self) -> None:
        """Recompute heavy capacity after external queue/traffic changes."""

        async with self._condition:
            self._rebalance_locked()
            self._condition.notify_all()

    def reset(self) -> None:
        """Drop pending work during a process restart; active callers are not cancelled here."""

        self._normal_queue.clear()
        self._heavy_queue.clear()
        self._normal_active = 0
        self._heavy_active = 0
        self._heavy_limit = self.heavy_workers


__all__ = [
    "USER_PRIORITY",
    "PrioritizedRequest",
    "PriorityRequestQueue",
    "QueueType",
    "priority_for_user_tier",
]
