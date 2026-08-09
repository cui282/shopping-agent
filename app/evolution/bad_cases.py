"""Reviewed bad-case capture without an automatic training loop.

The source design describes feeding failures into SFT and Agentic RL. This repository keeps the
useful observability boundary, but deliberately stops at human review: no training examples are
generated, no model weights are changed, and no live preference is mutated implicitly.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from threading import RLock
from typing import Literal

from pydantic import Field

from app.schemas import StrictModel

BadCaseSeverity = Literal["P0", "P1", "P2"]
BadCaseStatus = Literal["new", "reviewed", "accepted", "rejected"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class BadCase(StrictModel):
    bad_case_id: str = Field(pattern=r"^bad-[A-Za-z0-9_-]{8,80}$")
    trace_id: str = Field(min_length=1, max_length=160)
    query: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)
    rubric_score: float = Field(ge=0, le=1)
    severity: BadCaseSeverity
    status: BadCaseStatus = "new"
    tool_sequence: list[str] = Field(default_factory=list, max_length=32)
    reviewer: str | None = Field(default=None, max_length=120)
    review_notes: str | None = Field(default=None, max_length=2000)
    created_at: str = Field(default_factory=_now)


def _severity(score: float) -> BadCaseSeverity:
    if score < 0.4:
        return "P0"
    if score < 0.7:
        return "P1"
    return "P2"


def capture_bad_case(
    *,
    trace_id: str,
    query: str,
    reason: str,
    rubric_score: float,
    tool_sequence: list[str] | None = None,
) -> BadCase | None:
    """Capture only below-threshold traces for later human review."""

    if not 0 <= rubric_score < 0.8:
        return None
    seed = f"{trace_id}|{query}|{reason}".encode()
    bad_case_id = f"bad-{hashlib.sha256(seed).hexdigest()[:12]}"
    return BadCase(
        bad_case_id=bad_case_id,
        trace_id=trace_id,
        query=query,
        reason=reason,
        rubric_score=rubric_score,
        severity=_severity(rubric_score),
        tool_sequence=list(tool_sequence or [])[:32],
    )


class BadCaseLedger:
    """Process-local review queue; deployments may replace it with durable storage."""

    def __init__(self) -> None:
        self._items: dict[str, BadCase] = {}
        self._lock = RLock()

    def add(self, case: BadCase) -> BadCase:
        with self._lock:
            self._items[case.bad_case_id] = case
        return case

    def list(self, *, status: BadCaseStatus | None = None) -> tuple[BadCase, ...]:
        with self._lock:
            values = tuple(self._items.values())
        return tuple(item for item in values if status is None or item.status == status)

    def review(
        self,
        bad_case_id: str,
        *,
        status: Literal["reviewed", "accepted", "rejected"],
        reviewer: str,
        notes: str | None = None,
    ) -> BadCase:
        with self._lock:
            current = self._items[bad_case_id]
            updated = current.model_copy(
                update={"status": status, "reviewer": reviewer, "review_notes": notes}
            )
            self._items[bad_case_id] = updated
            return updated


bad_case_ledger = BadCaseLedger()


__all__ = [
    "BadCase",
    "BadCaseLedger",
    "BadCaseSeverity",
    "BadCaseStatus",
    "bad_case_ledger",
    "capture_bad_case",
]
