from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.api.monitor import Monitor
from app.utils.thread_ctx import get_session_dir, get_thread_id, thread_scope

T = TypeVar("T")


async def dispatch_tool(
    demands: list[dict[str, Any]],
    worker: Callable[[dict[str, Any]], Awaitable[T]],
    monitor: Monitor,
) -> list[T]:
    """Clone homogeneous sub-agent contexts and execute independent demands."""

    parent_thread_id = get_thread_id()
    directory = get_session_dir()

    async def run_branch(demand: dict[str, Any], index: int) -> T:
        sub_thread_id = f"sub-{uuid.uuid4().hex[:8]}"
        await monitor.emit(
            parent_thread_id,
            "fork",
            message=f"并行分支 {index + 1} 已启动",
            data={"sub_thread_id": sub_thread_id, "demand": demand},
        )
        with thread_scope(sub_thread_id, directory):
            return await worker(demand)

    tasks = [asyncio.create_task(run_branch(demand, index)) for index, demand in enumerate(demands)]
    return await asyncio.gather(*tasks)
