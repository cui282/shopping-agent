"""Typed model boundary for the Shopping Agent tool graph.

The registry describes the complete research tool set while ``task_tool`` is the only meta-tool
the model uses to schedule work. Every result-producing tool remains deterministic and receives
typed Product Evidence from the application, so model output cannot become evidence.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from app.agent.guard import bounded_tool_result, record_tool_call
from app.config import MARKETPLACES, get_settings
from app.schemas import AgentExecutionPlan, AgentStep, Platform, TaskToolCommand


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    phase: str
    model_callable: bool = False


FULL_TOOL_SET: tuple[ToolSpec, ...] = (
    ToolSpec("planner", "Resolve budget, category and hard constraints.", "think", True),
    ToolSpec("category_insight", "Retrieve structured category knowledge cards.", "think"),
    ToolSpec("item_search", "Search one licensed data-provider marketplace channel.", "act"),
    ToolSpec("recall", "Order only the supplied Product Evidence.", "act"),
    ToolSpec("price_compare", "Normalize supported currencies and rank prices.", "observe"),
    ToolSpec(
        "shipping_calc",
        "Estimate logistics and calculate import tax from typed customs evidence.",
        "observe",
    ),
    ToolSpec("item_picker", "Apply deterministic eligibility and ranking rules.", "reflect"),
    ToolSpec("shopping_summary", "Render the evidence-backed terminal result.", "reflect"),
    ToolSpec("web_search", "Optional external web evidence adapter.", "optional"),
    ToolSpec(
        "task_tool",
        "Schedule bounded parallel or isolated research work.",
        "orchestration",
        True,
    ),
)

_DEFAULT_STEPS: tuple[AgentStep, ...] = (
    "planner",
    "category_insight",
    "item_search",
    "recall",
    "price_compare",
    "shipping_calc",
    "item_picker",
    "shopping_summary",
)
_plan_var: ContextVar[AgentExecutionPlan | None] = ContextVar("agent_execution_plan", default=None)


def reset_execution_plan() -> None:
    _plan_var.set(None)


def get_execution_plan() -> AgentExecutionPlan | None:
    return _plan_var.get()


def _platforms(value: list[str] | tuple[str, ...] | None) -> list[Platform]:
    allowed = set(MARKETPLACES)
    selected: list[Platform] = []
    for item in value or ():
        name = str(item).strip().lower()
        if name in allowed and name not in selected:
            selected.append(name)  # type: ignore[arg-type]
    return selected


async def task_tool(
    platforms: list[str] | None = None,
    parallel: bool = True,
    steps: list[str] | None = None,
    reason: str = "",
) -> str:
    """Choose bounded research branches; the application executes all evidence tools."""

    command = TaskToolCommand(
        platforms=_platforms(platforms),
        parallel=parallel,
        steps=[step for step in (steps or []) if step in _DEFAULT_STEPS],
        reason=reason.strip(),
    )
    configured = set(get_settings().enabled_marketplaces)
    selected = [platform for platform in command.platforms if platform in configured]
    if not selected:
        selected = list(get_settings().enabled_marketplaces)
    normalized_steps = [step for step in command.steps if step != "task_tool"]
    if not normalized_steps:
        normalized_steps = list(_DEFAULT_STEPS)
    plan = AgentExecutionPlan(
        platforms=selected,
        fork=bool(command.parallel and len(selected) > 1),
        steps=normalized_steps,
        reason=command.reason,
        source="model",
    )
    record_tool_call(
        "task_tool",
        {
            "platforms": selected,
            "parallel": command.parallel,
            "steps": normalized_steps,
        },
    )
    _plan_var.set(plan)
    return bounded_tool_result(plan.model_dump(mode="json"))


def tool_specs() -> tuple[ToolSpec, ...]:
    return FULL_TOOL_SET


__all__ = [
    "FULL_TOOL_SET",
    "AgentExecutionPlan",
    "ToolSpec",
    "get_execution_plan",
    "reset_execution_plan",
    "task_tool",
    "tool_specs",
]
