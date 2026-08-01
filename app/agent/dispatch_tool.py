from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.api.monitor import Monitor
from app.config import get_settings
from app.schemas import DataMode, ForkEventData
from app.utils.thread_ctx import get_session_dir, get_thread_id, thread_scope

T = TypeVar("T")


async def dispatch_tool(
    demands: list[dict[str, Any]],
    worker: Callable[[dict[str, Any]], Awaitable[T]],
    monitor: Monitor,
    *,
    data_mode: DataMode | None = None,
) -> list[T]:
    """Clone homogeneous sub-agent contexts and execute independent demands."""

    parent_thread_id = get_thread_id()
    directory = get_session_dir()

    async def run_branch(demand: dict[str, Any], index: int) -> T:
        sub_thread_id = f"sub-{uuid.uuid4().hex[:8]}"
        platform = demand.get("platform")
        event_data = ForkEventData(
            sub_thread_id=sub_thread_id,
            platform=platform,
            demand=demand,
            data_mode=data_mode or get_settings().data_mode,
        )
        await monitor.emit(
            parent_thread_id,
            "fork",
            message=f"并行分支 {index + 1} 已启动：{event_data.platform}",
            data=event_data.model_dump(mode="json"),
        )
        with thread_scope(sub_thread_id, directory):
            return await worker(demand)

    tasks = [asyncio.create_task(run_branch(demand, index)) for index, demand in enumerate(demands)]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
