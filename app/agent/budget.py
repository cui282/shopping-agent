"""Request-scoped token accounting and conservative model-route selection."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Literal

from app.config import get_settings

TokenRoute = Literal["main", "lite", "minimal", "fallback"]


class TokenBudgetExceeded(RuntimeError):
    """The model boundary cannot spend more than the request budget."""


@dataclass(frozen=True, slots=True)
class TokenBudgetState:
    budget: int
    used: int = 0
    route: TokenRoute = "main"

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)


_state: ContextVar[TokenBudgetState | None] = ContextVar("token_budget_state", default=None)


def start_budget(budget: int | None = None) -> TokenBudgetState:
    value = budget or get_settings().token_budget
    state = TokenBudgetState(budget=max(256, value))
    _state.set(state)
    return state


def budget_state() -> TokenBudgetState:
    return _state.get() or start_budget()


def choose_route(estimated_tokens: int = 0) -> TokenRoute:
    state = budget_state()
    remaining = max(0, state.remaining - max(0, estimated_tokens))
    settings = get_settings()
    if remaining <= settings.token_route_minimal_threshold:
        route: TokenRoute = "minimal"
    elif remaining <= settings.token_route_lite_threshold:
        route = "lite"
    else:
        route = "main"
    _state.set(replace(state, route=route))
    return route


def record_usage(input_tokens: int = 0, output_tokens: int = 0) -> TokenBudgetState:
    state = budget_state()
    amount = max(0, int(input_tokens)) + max(0, int(output_tokens))
    next_state = replace(state, used=state.used + amount)
    _state.set(next_state)
    if next_state.used > next_state.budget:
        raise TokenBudgetExceeded(
            f"model token budget ({next_state.budget}) exceeded by {next_state.used - next_state.budget}"
        )
    return next_state


def route_model_name(route: TokenRoute) -> str:
    settings_name = {
        "main": "LLM_MAIN",
        "lite": "LLM_LITE",
        "minimal": "LLM_MINIMAL",
        "fallback": "LLM_FALLBACK",
    }[route]
    import os

    return os.getenv(settings_name, "").strip() or os.getenv("LLM_MAIN", "").strip()


__all__ = [
    "TokenBudgetExceeded",
    "TokenBudgetState",
    "TokenRoute",
    "budget_state",
    "choose_route",
    "record_usage",
    "route_model_name",
    "start_budget",
]
