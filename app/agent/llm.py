from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from app.config import get_settings


def requested_mode() -> str:
    return get_settings().agent_mode


def allow_rules_fallback() -> bool:
    return get_settings().allow_rules_fallback


def llm_is_configured() -> bool:
    return get_settings().llm_configured


def active_agent_mode() -> str:
    return get_settings().active_agent_mode


@lru_cache(maxsize=1)
def get_llm() -> Any:
    if not llm_is_configured():
        raise RuntimeError("OPENAI_API_KEY and LLM_MAIN are required for LLM execution")
    from langchain.chat_models import init_chat_model

    return init_chat_model(
        os.environ["LLM_MAIN"],
        model_provider="openai",
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.getenv("OPENAI_BASE_URL"),
        temperature=0.3,
    )
