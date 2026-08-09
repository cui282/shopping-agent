"""Conversation phase state machine and dynamic tool permissions."""

from __future__ import annotations

from contextvars import ContextVar
from enum import Enum


class Phase(str, Enum):
    PLANNING = "planning"
    SEARCHING = "searching"
    COMPARING = "comparing"
    CONCLUDING = "concluding"


PHASE_TOOLS: dict[Phase, frozenset[str]] = {
    Phase.PLANNING: frozenset({"planner", "task_tool", "category_insight", "chat_fallback"}),
    Phase.SEARCHING: frozenset(
        {"category_insight", "item_search", "recall", "task_tool", "web_search"}
    ),
    Phase.COMPARING: frozenset(
        {"item_search", "recall", "price_compare", "shipping_calc", "category_insight"}
    ),
    Phase.CONCLUDING: frozenset({"item_picker", "shopping_summary", "chat_fallback"}),
}

_phase_var: ContextVar[Phase] = ContextVar("shopping_agent_phase", default=Phase.PLANNING)
_history_var: ContextVar[tuple[str, ...]] = ContextVar("shopping_agent_tool_history", default=())


class PhaseStateMachine:
    """Per-task phase state with a conservative permission surface."""

    def get_current_phase(self) -> Phase:
        return _phase_var.get()

    def set_phase(self, phase: Phase) -> None:
        _phase_var.set(phase)

    def get_allowed_tools(self) -> frozenset[str]:
        return PHASE_TOOLS[self.get_current_phase()]

    def is_tool_allowed(self, tool_name: str) -> bool:
        return tool_name in self.get_allowed_tools()

    def tool_history(self) -> tuple[str, ...]:
        return _history_var.get()

    def observe_tool(self, tool_name: str) -> Phase:
        history = (*_history_var.get(), tool_name)
        _history_var.set(history[-32:])
        current = self.get_current_phase()
        if tool_name == "planner":
            current = Phase.SEARCHING
        elif tool_name == "recall":
            current = Phase.COMPARING
        elif tool_name == "shipping_calc":
            current = Phase.CONCLUDING
        self.set_phase(current)
        return current

    def transition(self, signal: str) -> bool:
        transitions = {
            (Phase.PLANNING, "planner_output_ready"): Phase.SEARCHING,
            (Phase.SEARCHING, "candidates_available"): Phase.COMPARING,
            (Phase.COMPARING, "picks_ready"): Phase.CONCLUDING,
            (Phase.COMPARING, "search_more"): Phase.SEARCHING,
        }
        target = transitions.get((self.get_current_phase(), signal))
        if target is None:
            return False
        self.set_phase(target)
        return True

    def reset(self) -> None:
        _phase_var.set(Phase.PLANNING)
        _history_var.set(())


phase_machine = PhaseStateMachine()


__all__ = ["PHASE_TOOLS", "Phase", "PhaseStateMachine", "phase_machine"]
