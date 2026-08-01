from __future__ import annotations

import pytest

from app.config import MARKETPLACES, ConfigurationError, get_settings


def _clear_marketplaces(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MARKETPLACES:
        monkeypatch.delenv(f"{name.upper()}_API_ENDPOINT", raising=False)
        monkeypatch.delenv(f"{name.upper()}_API_KEY", raising=False)


def test_live_runtime_requires_at_least_one_marketplace(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("AGENT_MODE", "rules")

    settings = get_settings()

    assert settings.status == "not_ready"
    assert not settings.task_ready
    assert settings.enabled_marketplaces == ()
    assert any("marketplace" in action for action in settings.required_actions)


def test_sandbox_is_explicit_and_never_production_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("SANDBOX_MODE", "true")
    monkeypatch.setenv("APP_ENV", "development")

    development = get_settings()

    assert development.task_ready
    assert development.status == "degraded"
    assert development.enabled_marketplaces == MARKETPLACES

    monkeypatch.setenv("APP_ENV", "production")
    production = get_settings()
    assert not production.task_ready
    assert production.status == "not_ready"
    assert "Disable SANDBOX_MODE in production" in production.required_actions


def test_fixture_fallback_is_never_production_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://gateway.example.com/amazon")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")

    settings = get_settings()

    assert not settings.task_ready
    assert not settings.fixture_fallback_enabled
    assert settings.status == "not_ready"
    assert "Disable ALLOW_FIXTURE_FALLBACK in production" in settings.required_actions


def test_mixed_diagnostic_mode_is_explicit_and_never_production_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("ALLOW_FIXTURE_FALLBACK", "true")
    monkeypatch.setenv("DEVELOPER_DIAGNOSTIC_MODE", "true")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://gateway.example.com/amazon")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")

    development = get_settings()
    assert development.fixture_fallback_enabled
    assert development.data_mode == "mixed"
    assert development.developer_diagnostic_mode
    assert development.status == "degraded"

    monkeypatch.setenv("APP_ENV", "production")
    production = get_settings()
    assert not production.task_ready
    assert not production.fixture_fallback_enabled
    assert "Disable DEVELOPER_DIAGNOSTIC_MODE in production" in production.required_actions


def test_partial_live_configuration_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://gateway.example.com/amazon")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")
    monkeypatch.setenv("EBAY_API_ENDPOINT", "https://gateway.example.com/ebay")

    settings = get_settings()

    assert settings.task_ready
    assert settings.status == "degraded"
    assert settings.enabled_marketplaces == ("amazon",)
    assert any("EBAY_API_KEY" in action for action in settings.required_actions)


def test_invalid_boolean_configuration_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_MODE", "sometimes")

    with pytest.raises(ConfigurationError, match="SANDBOX_MODE"):
        get_settings()


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numeric_configuration_fails_fast(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TASK_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigurationError, match="TASK_TIMEOUT_SECONDS"):
        get_settings()
