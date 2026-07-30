from __future__ import annotations

from typing import Any

from app.api.connection import ConnectionManager, manager
from app.schemas import EventName, MonitorEvent

_MESSAGES = {
    "session_created": "购物任务已创建",
    "assistant_call": "Agent 正在分析",
    "tool_start": "正在调用工具",
    "tool_end": "工具调用完成",
    "fork": "已创建并行检索分支",
    "task_result": "购物建议已生成",
    "task_cancelled": "购物任务已取消",
    "error": "购物任务执行失败",
}


class Monitor:
    def __init__(self, connections: ConnectionManager = manager) -> None:
        self.connections = connections

    async def emit(
        self,
        thread_id: str,
        event: EventName,
        *,
        message: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> MonitorEvent:
        envelope = MonitorEvent(
            event=event,
            message=message or _MESSAGES[event],
            data=data or {},
        )
        await self.connections.send_to_thread(thread_id, envelope.model_dump(mode="json"))
        return envelope


monitor = Monitor()
