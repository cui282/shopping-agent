from __future__ import annotations

import pytest

from app.config import MARKETPLACES, ConfigurationError, get_settings


def _clear_marketplaces(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MARKETPLACES:
        monkeypatch.delenv(f"{name.upper()}_DATA_PROVIDER", raising=False)
        monkeypatch.delenv(f"{name.upper()}_DATA_CHANNEL_ENDPOINT", raising=False)
        monkeypatch.delenv(f"{name.upper()}_DATA_CHANNEL_CREDENTIAL", raising=False)
        monkeypatch.delenv(f"{name.upper()}_API_ENDPOINT", raising=False)
        monkeypatch.delenv(f"{name.upper()}_API_KEY", raising=False)


def test_data_provider_channel_configuration_is_preferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv(
        "AMAZON_DATA_CHANNEL_ENDPOINT", "https://provider.example.com/channels/amazon/search"
    )
    monkeypatch.setenv("AMAZON_DATA_CHANNEL_CREDENTIAL", "provider-channel-token")
    monkeypatch.setenv("AMAZON_DATA_PROVIDER", "licensed-catalog-vendor")

    settings = get_settings()

    amazon = settings.marketplaces[0]
    assert amazon.name == "amazon"
    assert amazon.endpoint == "https://provider.example.com/channels/amazon/search"
    assert amazon.credential == "provider-channel-token"
    assert amazon.provider == "licensed-catalog-vendor"
    assert amazon.configured


def test_conflicting_legacy_and_channel_credentials_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("AMAZON_DATA_CHANNEL_ENDPOINT", "https://provider.example.com/amazon")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://legacy.example.com/amazon")

    with pytest.raises(ConfigurationError, match="AMAZON_DATA_CHANNEL_ENDPOINT"):
        get_settings()


def test_channel_endpoint_and_credential_cannot_be_combined_from_partial_alias_families(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("AMAZON_DATA_CHANNEL_ENDPOINT", "https://provider.example.com/amazon")
    monkeypatch.setenv("AMAZON_API_KEY", "legacy-credential")

    with pytest.raises(ConfigurationError, match="same variable family"):
        get_settings()


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


def test_memory_preferences_are_not_production_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_marketplaces(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SANDBOX_MODE", "false")
    monkeypatch.setenv("STORE_BACKEND", "memory")
    monkeypatch.setenv("AMAZON_API_ENDPOINT", "https://gateway.example.com/amazon")
    monkeypatch.setenv("AMAZON_API_KEY", "test-key")

    settings = get_settings()

    assert not settings.task_ready
    assert settings.status == "not_ready"
    assert (
        "Use STORE_BACKEND=redis for persistent production preferences" in settings.required_actions
    )


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


def test_invalid_recall_configuration_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANN_BACKEND", "vector-db")

    with pytest.raises(ConfigurationError, match="ANN_BACKEND"):
        get_settings()
