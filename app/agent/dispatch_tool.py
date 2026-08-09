from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.api.monitor import Monitor
from app.config import get_settings
from app.observability import get_observer
from app.schemas import DataMode, ForkEventData
from app.utils.thread_ctx import get_fork_depth, get_session_dir, get_thread_id, thread_scope

T = TypeVar("T")

_child_slots: dict[int, tuple[int, asyncio.Semaphore]] = {}


def _global_child_slot(limit: int) -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    current = _child_slots.get(loop_id)
    if current is None or current[0] != limit:
        semaphore = asyncio.Semaphore(limit)
        _child_slots[loop_id] = (limit, semaphore)
        return semaphore
    return current[1]


def reset_child_slots() -> None:
    _child_slots.clear()


async def dispatch_tool(
    demands: list[dict[str, Any]],
    worker: Callable[[dict[str, Any]], Awaitable[T]],
    monitor: Monitor,
    *,
    data_mode: DataMode | None = None,
) -> list[T]:
    """Clone homogeneous sub-agent contexts and execute bounded independent demands.

    The worker remains typed and authoritative. The guardrails here only constrain how a
    model-driven controller can fan out work; they never turn a failed branch into fabricated
    Product Evidence.
    """

    parent_thread_id = get_thread_id()
    directory = get_session_dir()
    settings = get_settings()
    parent_depth = get_fork_depth()
    if parent_depth >= settings.agent_max_fork_depth:
        raise RuntimeError(
            f"fork depth limit ({settings.agent_max_fork_depth}) reached for {parent_thread_id}"
        )
    if not demands:
        return []
    if len(demands) > settings.agent_max_children:
        raise RuntimeError(
            f"child limit ({settings.agent_max_children}) exceeded for {parent_thread_id}"
        )
    child_slot = _global_child_slot(settings.agent_max_concurrent_children)

    async def run_branch(demand: dict[str, Any], index: int) -> T:
        sub_thread_id = f"sub-{uuid.uuid4().hex[:8]}"
        platform = demand.get("platform")
        event_data = ForkEventData(
            sub_thread_id=sub_thread_id,
            platform=platform,
            demand=demand,
            data_mode=data_mode or get_settings().data_mode,
        )
        async with child_slot:
            await monitor.emit(
                parent_thread_id,
                "fork",
                message=f"并行分支 {index + 1} 已启动：{event_data.platform}",
                data=event_data.model_dump(mode="json"),
            )
            observer = get_observer()
            observer.start_child_trace(
                parent_thread_id,
                sub_thread_id,
                platform=event_data.platform,
                demand_keys=sorted(demand),
                fork_depth=parent_depth + 1,
            )
            try:
                with thread_scope(sub_thread_id, directory, fork_depth=parent_depth + 1):
                    result = await asyncio.wait_for(
                        worker(demand), timeout=settings.agent_child_timeout_seconds
                    )
            except asyncio.CancelledError:
                observer.end_child_trace(sub_thread_id, status="cancelled")
                raise
            except Exception:
                observer.end_child_trace(sub_thread_id, status="error")
                raise
            observer.end_child_trace(sub_thread_id, status="ok")
            return result

    tasks = [asyncio.create_task(run_branch(demand, index)) for index, demand in enumerate(demands)]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
