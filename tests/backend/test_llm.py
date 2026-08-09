from __future__ import annotations

from typing import Any

from app.agent import llm


def test_get_llm_passes_configured_reasoning_effort(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model: str, **kwargs: Any) -> object:
        captured["model"] = model
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MAIN", "gpt5.6luna")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.pingcodes.cc/v1")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "max")
    monkeypatch.setattr("langchain.chat_models.init_chat_model", fake_init_chat_model)
    llm.get_llm.cache_clear()

    try:
        llm.get_llm()
    finally:
        llm.get_llm.cache_clear()

    assert captured["model"] == "gpt5.6luna"
    assert captured["reasoning_effort"] == "max"
    assert captured["base_url"] == "https://api.pingcodes.cc/v1"


def test_get_llm_supports_responses_api_without_storage(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_init_chat_model(model: str, **kwargs: Any) -> object:
        captured["model"] = model
        captured.update(kwargs)
        return object()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MAIN", "gpt-5.5")
    monkeypatch.setenv("LLM_WIRE_API", "responses")
    monkeypatch.setenv("LLM_RESPONSE_STORAGE", "false")
    monkeypatch.setattr("langchain.chat_models.init_chat_model", fake_init_chat_model)
    llm.get_llm.cache_clear()

    try:
        llm.get_llm()
    finally:
        llm.get_llm.cache_clear()

    assert captured["model"] == "gpt-5.5"
    assert captured["use_responses_api"] is True
    assert captured["store"] is False
