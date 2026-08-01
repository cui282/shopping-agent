from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.agent.dispatch_tool import dispatch_tool
from app.utils.thread_ctx import thread_scope


class RecordingMonitor:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict[str, Any]]] = []

    async def emit(
        self,
        thread_id: str,
        event: str,
        *,
        message: str,
        data: dict[str, Any],
    ) -> None:
        self.events.append((thread_id, event, data))


@pytest.mark.asyncio
async def test_dispatch_cancels_and_awaits_siblings_when_one_branch_fails(
    tmp_path: Path,
) -> None:
    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    release_sibling = asyncio.Event()
    monitor = RecordingMonitor()

    async def worker(demand: dict[str, Any]) -> str:
        if demand["platform"] == "amazon":
            await sibling_started.wait()
            raise RuntimeError("provider failed")
        sibling_started.set()
        try:
            await release_sibling.wait()
            return "late result"
        finally:
            if not release_sibling.is_set():
                sibling_cancelled.set()

    try:
        with (
            thread_scope("thread-dispatch", tmp_path),
            pytest.raises(RuntimeError, match="provider failed"),
        ):
            await dispatch_tool(
                [
                    {"platform": "amazon", "query": "耳机"},
                    {"platform": "ebay", "query": "耳机"},
                ],
                worker,
                monitor,  # type: ignore[arg-type]
            )
        assert sibling_cancelled.is_set()
    finally:
        release_sibling.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_dispatch_overlaps_enabled_marketplace_branches(tmp_path: Path) -> None:
    branch_count = 4
    all_started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0
    monitor = RecordingMonitor()

    async def worker(_demand: dict[str, Any]) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == branch_count:
            all_started.set()
        try:
            await release.wait()
            return "result"
        finally:
            active -= 1

    async def release_when_ready() -> None:
        await all_started.wait()
        release.set()

    try:
        with thread_scope("thread-overlap", tmp_path):
            results = await asyncio.wait_for(
                asyncio.gather(
                    dispatch_tool(
                        [
                            {"platform": name, "query": "耳机"}
                            for name in ("amazon", "shopee", "aliexpress", "ebay")
                        ],
                        worker,
                        monitor,  # type: ignore[arg-type]
                    ),
                    release_when_ready(),
                ),
                timeout=1,
            )
        assert results[0] == ["result"] * branch_count
        assert max_active == branch_count
    finally:
        release.set()
