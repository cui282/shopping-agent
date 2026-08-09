from __future__ import annotations

import asyncio

import pytest

from app.resilience.request_queue import (
    PrioritizedRequest,
    PriorityRequestQueue,
    priority_for_user_tier,
)


def _request(name: str, priority: int = 5) -> PrioritizedRequest:
    return PrioritizedRequest(
        priority=priority,
        timestamp=float(len(name)),
        thread_id=name,
        query=name,
        user_id=name,
    )


def test_queue_classifies_turns_and_user_priority() -> None:
    queue = PriorityRequestQueue(normal_workers=2, heavy_workers=1)

    assert queue.classify(29) == "normal"
    assert queue.classify(30) == "heavy"
    assert priority_for_user_tier("premium") < priority_for_user_tier("free")
    assert priority_for_user_tier("untrusted") == priority_for_user_tier("standard")


@pytest.mark.asyncio
async def test_queue_serves_lower_priority_first() -> None:
    queue = PriorityRequestQueue(normal_workers=1, heavy_workers=1)
    low = _request("low", priority=10)
    high = _request("high", priority=1)
    await queue.enqueue(low, "normal")
    await queue.enqueue(high, "normal")

    served: list[str] = []
    async with queue.acquire(high, "normal"):
        served.append(high.thread_id)
    async with queue.acquire(low, "normal"):
        served.append(low.thread_id)

    assert served == ["high", "low"]
    assert queue.pending == 0
    assert queue.active == 0


@pytest.mark.asyncio
async def test_cancelled_waiter_is_removed_and_rebalance_limits_heavy_work() -> None:
    queue = PriorityRequestQueue(normal_workers=1, heavy_workers=4, rebalance_threshold=1)
    normal_holder = _request("holder")
    normal_waiter = _request("waiter")
    await queue.enqueue(normal_holder, "normal")
    await queue.enqueue(normal_waiter, "normal")
    assert queue._heavy_limit == 2

    async with queue.acquire(normal_holder, "normal"):
        waiter = asyncio.create_task(_wait_for(queue, normal_waiter))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert queue.pending == 0

    assert queue._heavy_limit == 4


async def _wait_for(queue: PriorityRequestQueue, request: PrioritizedRequest) -> None:
    async with queue.acquire(request, "normal"):
        return


@pytest.mark.asyncio
async def test_run_defers_coroutine_creation_until_admission() -> None:
    queue = PriorityRequestQueue(normal_workers=1, heavy_workers=1)
    request = _request("run")
    called = False

    async def operation() -> str:
        nonlocal called
        called = True
        return "ok"

    assert await queue.run(request, "normal", operation) == "ok"
    assert called
