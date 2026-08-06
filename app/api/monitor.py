from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.api.connection import ConnectionManager, manager
from app.schemas import EventName, MonitorEvent
from app.utils.thread_ctx import get_run_id

_MESSAGES = {
    "session_created": "购物任务已创建",
    "intent_resolved": "研究意图已保存",
    "assistant_call": "Agent 正在分析",
    "tool_start": "正在调用工具",
    "tool_end": "工具调用完成",
    "fork": "已创建并行检索分支",
    "report_generated": "研究报告已生成",
    "task_result": "购物建议已生成",
    "task_cancelled": "购物任务已取消",
    "clarification_required": "等待澄清",
    "clarification_resolved": "已收到澄清回答",
    "error": "购物任务执行失败",
}

EventRecorder = Callable[
    [str, EventName, str, dict[str, Any], str, str, str | None],
    MonitorEvent | tuple[MonitorEvent, ...],
]


class Monitor:
    def __init__(
        self,
        connections: ConnectionManager = manager,
        event_recorder: EventRecorder | None = None,
    ) -> None:
        self.connections = connections
        self._event_recorder = event_recorder
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._sequences: defaultdict[str, int] = defaultdict(int)
        self._run_ids: defaultdict[str, str] = defaultdict(lambda: uuid.uuid4().hex)

    def set_event_recorder(self, event_recorder: EventRecorder) -> None:
        self._event_recorder = event_recorder

    def discard(self, thread_id: str) -> None:
        """Forget process-local sequencing state after durable task deletion."""
        self._locks.pop(thread_id, None)
        self._sequences.pop(thread_id, None)
        self._run_ids.pop(thread_id, None)

    async def emit(
        self,
        thread_id: str,
        event: EventName,
        *,
        message: str | None = None,
        data: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> MonitorEvent:
        async with self._locks[thread_id]:
            event_id = f"evt-{uuid.uuid4().hex}"
            timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            event_message = message or _MESSAGES[event]
            event_data = data or {}
            event_run_id = run_id or get_run_id()
            if self._event_recorder is not None:
                recorded = self._event_recorder(
                    thread_id,
                    event,
                    event_message,
                    event_data,
                    event_id,
                    timestamp,
                    event_run_id,
                )
            else:
                self._sequences[thread_id] += 1
                recorded = MonitorEvent(
                    event_id=event_id,
                    thread_id=thread_id,
                    run_id=event_run_id or self._run_ids[thread_id],
                    sequence=self._sequences[thread_id],
                    event=event,
                    message=event_message,
                    data=event_data,
                    timestamp=timestamp,
                )
            envelopes = recorded if isinstance(recorded, tuple) else (recorded,)
            for envelope in envelopes:
                await self.connections.send_to_thread(thread_id, envelope.model_dump(mode="json"))
        return envelopes[-1]


monitor = Monitor()
