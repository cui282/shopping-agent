from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Literal

AppEnvironment = Literal["development", "test", "production"]
AgentMode = Literal["auto", "llm", "rules"]
RuntimeStatus = Literal["ready", "degraded", "not_ready"]

MARKETPLACES = ("amazon", "shopee", "aliexpress", "ebay")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class ConfigurationError(RuntimeError):
    pass


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


def _choice(name: str, default: str, allowed: set[str]) -> str:
    value = os.getenv(name, default).strip().lower()
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ConfigurationError(f"{name} must be one of: {choices}")
    return value


def _number(name: str, default: float, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum:g}")
    return value


def _integer(name: str, default: int, minimum: int) -> int:
    value = _number(name, float(default), float(minimum))
    if not value.is_integer():
        raise ConfigurationError(f"{name} must be an integer")
    return int(value)


@dataclass(frozen=True, slots=True)
class MarketplaceSettings:
    name: str
    endpoint: str
    api_key: str

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.api_key)

    @property
    def state(self) -> Literal["configured", "partial", "missing"]:
        if self.configured:
            return "configured"
        if self.endpoint or self.api_key:
            return "partial"
        return "missing"


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: AppEnvironment
    agent_mode: AgentMode
    sandbox_mode: bool
    allow_fixture_fallback: bool
    allow_rules_fallback: bool
    store_backend: Literal["memory", "redis"]
    provider_timeout_seconds: float
    task_timeout_seconds: float
    max_concurrent_tasks: int
    task_retention_seconds: int
    preference_ttl_seconds: int
    max_upload_bytes: int
    cors_origins: tuple[str, ...]
    marketplaces: tuple[MarketplaceSettings, ...]

    @classmethod
    def from_env(cls) -> Settings:
        app_env = _choice("APP_ENV", "development", {"development", "test", "production"})
        agent_mode = _choice("AGENT_MODE", "auto", {"auto", "llm", "rules"})
        origins = tuple(
            value.strip()
            for value in os.getenv(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ).split(",")
            if value.strip()
        )
        marketplaces = tuple(
            MarketplaceSettings(
                name=name,
                endpoint=os.getenv(f"{name.upper()}_API_ENDPOINT", "").strip(),
                api_key=os.getenv(f"{name.upper()}_API_KEY", "").strip(),
            )
            for name in MARKETPLACES
        )
        return cls(
            app_env=app_env,  # type: ignore[arg-type]
            agent_mode=agent_mode,  # type: ignore[arg-type]
            sandbox_mode=_boolean("SANDBOX_MODE", False),
            allow_fixture_fallback=_boolean("ALLOW_FIXTURE_FALLBACK", False),
            allow_rules_fallback=_boolean("ALLOW_RULES_FALLBACK", True),
            store_backend=_choice("STORE_BACKEND", "memory", {"memory", "redis"}),  # type: ignore[arg-type]
            provider_timeout_seconds=_number("PROVIDER_TIMEOUT_SECONDS", 15, 1),
            task_timeout_seconds=_number("TASK_TIMEOUT_SECONDS", 180, 10),
            max_concurrent_tasks=_integer("MAX_CONCURRENT_TASKS", 8, 1),
            task_retention_seconds=_integer("TASK_RETENTION_SECONDS", 86_400, 60),
            preference_ttl_seconds=_integer("PREFERENCE_TTL_SECONDS", 31_536_000, 60),
            max_upload_bytes=_integer("UPLOAD_MAX_BYTES", 8 * 1024 * 1024, 1024),
            cors_origins=origins,
            marketplaces=marketplaces,
        )

    @property
    def llm_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", "").strip() and os.getenv("LLM_MAIN", "").strip())

    @property
    def active_agent_mode(self) -> Literal["llm", "rules", "unavailable"]:
        if self.agent_mode == "rules":
            return "rules"
        if self.llm_configured:
            return "llm"
        if self.agent_mode == "auto" or self.allow_rules_fallback:
            return "rules"
        return "unavailable"

    @property
    def configured_marketplaces(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.marketplaces if item.configured)

    @property
    def enabled_marketplaces(self) -> tuple[str, ...]:
        if self.sandbox_mode:
            return MARKETPLACES
        return self.configured_marketplaces

    @property
    def fixture_fallback_enabled(self) -> bool:
        return self.allow_fixture_fallback and self.app_env != "production"

    @property
    def task_ready(self) -> bool:
        if self.active_agent_mode == "unavailable":
            return False
        if self.app_env == "production" and (self.sandbox_mode or self.allow_fixture_fallback):
            return False
        return bool(self.enabled_marketplaces)

    @property
    def required_actions(self) -> tuple[str, ...]:
        actions: list[str] = []
        if self.active_agent_mode == "unavailable":
            actions.append("Configure OPENAI_API_KEY and LLM_MAIN, or enable ALLOW_RULES_FALLBACK")
        if self.app_env == "production" and self.sandbox_mode:
            actions.append("Disable SANDBOX_MODE in production")
        if self.app_env == "production" and self.allow_fixture_fallback:
            actions.append("Disable ALLOW_FIXTURE_FALLBACK in production")
        if not self.enabled_marketplaces:
            actions.append(
                "Configure at least one marketplace endpoint/key pair, or explicitly enable SANDBOX_MODE for local testing"
            )
        for marketplace in self.marketplaces:
            if marketplace.state == "partial":
                actions.append(
                    f"Complete both {marketplace.name.upper()}_API_ENDPOINT and {marketplace.name.upper()}_API_KEY"
                )
        if self.app_env == "production" and self.store_backend == "memory":
            actions.append("Use STORE_BACKEND=redis for persistent production preferences")
        return tuple(actions)

    @property
    def status(self) -> RuntimeStatus:
        if not self.task_ready:
            return "not_ready"
        if (
            self.sandbox_mode
            or len(self.configured_marketplaces) < len(MARKETPLACES)
            or (self.app_env == "production" and self.store_backend == "memory")
        ):
            return "degraded"
        return "ready"


def get_settings() -> Settings:
    return Settings.from_env()
