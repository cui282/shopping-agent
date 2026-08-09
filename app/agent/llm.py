from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.agent.budget import TokenRoute, route_model_name
from app.config import get_settings


def requested_mode() -> str:
    return get_settings().agent_mode


def allow_rules_fallback() -> bool:
    return get_settings().allow_rules_fallback


def llm_is_configured() -> bool:
    return get_settings().llm_configured


def active_agent_mode() -> str:
    return get_settings().active_agent_mode


@lru_cache(maxsize=4)
def get_llm(route: TokenRoute = "main") -> Any:
    if not llm_is_configured():
        raise RuntimeError("OPENAI_API_KEY and LLM_MAIN are required for LLM execution")
    from langchain.chat_models import init_chat_model

    kwargs: dict[str, Any] = {
        "model_provider": "openai",
        "api_key": os.environ["OPENAI_API_KEY"],
        "base_url": os.getenv("OPENAI_BASE_URL"),
        "temperature": 0.3,
    }
    wire_api = os.getenv("LLM_WIRE_API", "chat_completions").strip().lower()
    if wire_api not in {"chat_completions", "responses"}:
        raise RuntimeError("LLM_WIRE_API must be chat_completions or responses")
    if wire_api == "responses":
        kwargs["use_responses_api"] = True

    response_storage = os.getenv("LLM_RESPONSE_STORAGE", "").strip().lower()
    if response_storage:
        if response_storage not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
            raise RuntimeError("LLM_RESPONSE_STORAGE must be a boolean value")
        kwargs["store"] = response_storage in {"1", "true", "yes", "on"}

    reasoning_effort = os.getenv("LLM_REASONING_EFFORT", "").strip()
    if reasoning_effort:
        # Keep this optional because some OpenAI-compatible gateways do not
        # implement the reasoning_effort request parameter.
        kwargs["reasoning_effort"] = reasoning_effort

    model_name = route_model_name(route)
    if not model_name:
        raise RuntimeError("LLM_MAIN is required for LLM execution")
    return init_chat_model(
        model_name,
        **kwargs,
    )
