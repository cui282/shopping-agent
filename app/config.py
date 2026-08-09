from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Literal

AppEnvironment = Literal["development", "test", "production"]
AgentMode = Literal["auto", "llm", "rules"]
RuntimeStatus = Literal["ready", "degraded", "not_ready"]
AnnBackend = Literal["disabled", "faiss"]
RecallArchitecture = Literal["dual", "legacy_three"]

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


def _percentage(name: str, default: int) -> int:
    value = _integer(name, default, 0)
    if value > 100:
        raise ConfigurationError(f"{name} must be at most 100")
    return value


def _fraction(name: str, default: float) -> float:
    value = _number(name, default, 0)
    if value > 1:
        raise ConfigurationError(f"{name} must be at most 1")
    return value


def _aliased_channel_value(preferred_name: str, legacy_name: str) -> str:
    preferred = os.getenv(preferred_name, "").strip()
    legacy = os.getenv(legacy_name, "").strip()
    if preferred and legacy and preferred != legacy:
        raise ConfigurationError(
            f"{preferred_name} conflicts with legacy alias {legacy_name}; configure only one value"
        )
    return preferred or legacy


@dataclass(frozen=True, slots=True)
class DataProviderChannelSettings:
    name: str
    endpoint: str
    credential: str
    provider: str

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.credential)

    @property
    def state(self) -> Literal["configured", "partial", "missing"]:
        if self.configured:
            return "configured"
        if self.endpoint or self.credential:
            return "partial"
        return "missing"


def _data_provider_channel(name: str) -> DataProviderChannelSettings:
    prefix = name.upper()
    preferred_endpoint = os.getenv(f"{prefix}_DATA_CHANNEL_ENDPOINT", "").strip()
    preferred_credential = os.getenv(f"{prefix}_DATA_CHANNEL_CREDENTIAL", "").strip()
    legacy_endpoint = os.getenv(f"{prefix}_API_ENDPOINT", "").strip()
    legacy_credential = os.getenv(f"{prefix}_API_KEY", "").strip()
    if (
        (preferred_endpoint or preferred_credential)
        and (legacy_endpoint or legacy_credential)
        and not (preferred_endpoint and preferred_credential)
        and not (legacy_endpoint and legacy_credential)
    ):
        raise ConfigurationError(
            f"{prefix}_DATA_CHANNEL_ENDPOINT and {prefix}_DATA_CHANNEL_CREDENTIAL "
            f"must use the same variable family as {prefix}_API_ENDPOINT and {prefix}_API_KEY"
        )
    return DataProviderChannelSettings(
        name=name,
        endpoint=_aliased_channel_value(
            f"{prefix}_DATA_CHANNEL_ENDPOINT", f"{prefix}_API_ENDPOINT"
        ),
        credential=_aliased_channel_value(f"{prefix}_DATA_CHANNEL_CREDENTIAL", f"{prefix}_API_KEY"),
        provider=os.getenv(f"{prefix}_DATA_PROVIDER", f"{name}-data-provider-channel").strip()
        or f"{name}-data-provider-channel",
    )


@dataclass(frozen=True, slots=True)
class Settings:
    app_env: AppEnvironment
    agent_mode: AgentMode
    sandbox_mode: bool
    allow_fixture_fallback: bool
    developer_diagnostic_mode: bool
    allow_rules_fallback: bool
    store_backend: Literal["memory", "redis"]
    provider_timeout_seconds: float
    task_timeout_seconds: float
    max_concurrent_tasks: int
    agent_max_children: int
    agent_max_concurrent_children: int
    task_retention_seconds: int
    preference_ttl_seconds: int
    max_upload_bytes: int
    cors_origins: tuple[str, ...]
    auth_enabled: bool
    auth_user_header: str
    auth_tenant_header: str
    auth_signature_header: str
    auth_shared_secret: str
    rate_limit_enabled: bool
    rate_limit_requests: int
    rate_limit_window_seconds: float
    shutdown_grace_seconds: float
    release_channel: Literal["stable", "canary"]
    release_id: str
    release_traffic_percent: int
    release_rollback: bool
    marketplaces: tuple[DataProviderChannelSettings, ...]
    ann_backend: AnnBackend
    recall_architecture: RecallArchitecture
    ann_index_path: str
    tower_query_endpoint: str
    tower_item_endpoint: str
    tower_user_endpoint: str
    reranker_endpoint: str
    opensearch_url: str
    opensearch_category_index: str
    opensearch_search_pipeline: str
    recall_timeout_seconds: float
    rerank_timeout_seconds: float
    compress_keep_recent: int
    compress_max_tokens: int
    agent_max_fork_depth: int
    agent_child_timeout_seconds: float
    agent_max_tool_calls: int
    agent_tool_result_chars: int
    agent_loop_detection_threshold: int
    provider_retry_attempts: int
    provider_retry_backoff_seconds: float
    provider_retry_backoff_max_seconds: float
    provider_circuit_failure_threshold: int
    provider_circuit_window_size: int
    provider_circuit_failure_rate: float
    provider_circuit_reset_seconds: float
    provider_max_concurrency: int
    token_budget: int
    token_route_lite_threshold: int
    token_route_minimal_threshold: int
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_base_url: str

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
        marketplaces = tuple(_data_provider_channel(name) for name in MARKETPLACES)
        recall_architecture = os.getenv("RECALL_ARCHITECTURE", "").strip().lower()
        if not recall_architecture and os.getenv("TOWER_USER_ENDPOINT", "").strip():
            # Backward compatibility for deployments created before the dual-tower switch.
            recall_architecture = "legacy_three"
        recall_architecture = recall_architecture or "dual"
        return cls(
            app_env=app_env,  # type: ignore[arg-type]
            agent_mode=agent_mode,  # type: ignore[arg-type]
            sandbox_mode=_boolean("SANDBOX_MODE", False),
            allow_fixture_fallback=_boolean("ALLOW_FIXTURE_FALLBACK", False),
            developer_diagnostic_mode=_boolean("DEVELOPER_DIAGNOSTIC_MODE", False),
            allow_rules_fallback=_boolean("ALLOW_RULES_FALLBACK", True),
            store_backend=_choice("STORE_BACKEND", "memory", {"memory", "redis"}),  # type: ignore[arg-type]
            provider_timeout_seconds=_number("PROVIDER_TIMEOUT_SECONDS", 15, 1),
            task_timeout_seconds=_number("TASK_TIMEOUT_SECONDS", 180, 10),
            max_concurrent_tasks=_integer("MAX_CONCURRENT_TASKS", 8, 1),
            agent_max_children=_integer("AGENT_MAX_CHILDREN", 4, 1),
            agent_max_concurrent_children=_integer("AGENT_MAX_CONCURRENT_CHILDREN", 8, 1),
            task_retention_seconds=_integer("TASK_RETENTION_SECONDS", 86_400, 60),
            preference_ttl_seconds=_integer("PREFERENCE_TTL_SECONDS", 31_536_000, 60),
            max_upload_bytes=_integer("UPLOAD_MAX_BYTES", 8 * 1024 * 1024, 1024),
            cors_origins=origins,
            auth_enabled=_boolean("AUTH_ENABLED", False),
            auth_user_header=os.getenv("AUTH_USER_HEADER", "X-Auth-User").strip() or "X-Auth-User",
            auth_tenant_header=os.getenv("AUTH_TENANT_HEADER", "X-Auth-Tenant").strip()
            or "X-Auth-Tenant",
            auth_signature_header=os.getenv("AUTH_SIGNATURE_HEADER", "X-Auth-Signature").strip()
            or "X-Auth-Signature",
            auth_shared_secret=os.getenv("AUTH_SHARED_SECRET", "").strip(),
            rate_limit_enabled=_boolean("RATE_LIMIT_ENABLED", False),
            rate_limit_requests=_integer("RATE_LIMIT_REQUESTS", 120, 1),
            rate_limit_window_seconds=_number("RATE_LIMIT_WINDOW_SECONDS", 60, 1),
            shutdown_grace_seconds=_number(
                "SHUTDOWN_GRACE_SECONDS", 0 if app_env == "test" else 15, 0
            ),
            release_channel=_choice("RELEASE_CHANNEL", "stable", {"stable", "canary"}),  # type: ignore[arg-type]
            release_id=os.getenv("RELEASE_ID", "local").strip() or "local",
            release_traffic_percent=_percentage("RELEASE_TRAFFIC_PERCENT", 100),
            release_rollback=_boolean("RELEASE_ROLLBACK", False),
            marketplaces=marketplaces,
            ann_backend=_choice("ANN_BACKEND", "disabled", {"disabled", "faiss"}),  # type: ignore[arg-type]
            recall_architecture=_choice(
                "RECALL_ARCHITECTURE", recall_architecture, {"dual", "legacy_three"}
            ),  # type: ignore[arg-type]
            ann_index_path=os.getenv("ANN_INDEX_PATH", "./data/item_index.faiss").strip(),
            tower_query_endpoint=os.getenv("TOWER_QUERY_ENDPOINT", "").strip(),
            tower_item_endpoint=os.getenv("TOWER_ITEM_ENDPOINT", "").strip(),
            tower_user_endpoint=os.getenv("TOWER_USER_ENDPOINT", "").strip(),
            reranker_endpoint=os.getenv("RERANKER_ENDPOINT", "").strip(),
            opensearch_url=os.getenv("OPENSEARCH_URL", "").strip(),
            opensearch_category_index=os.getenv(
                "OPENSEARCH_CATEGORY_INDEX", "shopping_agent_category_kb"
            ).strip(),
            opensearch_search_pipeline=os.getenv(
                "OPENSEARCH_SEARCH_PIPELINE", "shopping-agent-hybrid-pipeline"
            ).strip(),
            recall_timeout_seconds=_number("RECALL_TIMEOUT_SECONDS", 10, 1),
            rerank_timeout_seconds=_number("RERANK_TIMEOUT_SECONDS", 10, 1),
            compress_keep_recent=_integer("COMPRESS_KEEP_RECENT", 3, 1),
            compress_max_tokens=_integer("COMPRESS_MAX_TOKENS", 12_000, 32),
            agent_max_fork_depth=_integer("AGENT_MAX_FORK_DEPTH", 2, 0),
            agent_child_timeout_seconds=_number("AGENT_CHILD_TIMEOUT_SECONDS", 90, 1),
            agent_max_tool_calls=_integer("AGENT_MAX_TOOL_CALLS", 24, 1),
            agent_tool_result_chars=_integer("AGENT_TOOL_RESULT_CHARS", 16_000, 256),
            agent_loop_detection_threshold=_integer("AGENT_LOOP_DETECTION_THRESHOLD", 4, 2),
            provider_retry_attempts=_integer("PROVIDER_RETRY_ATTEMPTS", 2, 0),
            provider_retry_backoff_seconds=_number("PROVIDER_RETRY_BACKOFF_SECONDS", 0.2, 0.01),
            provider_retry_backoff_max_seconds=_number(
                "PROVIDER_RETRY_BACKOFF_MAX_SECONDS", 2, 0.01
            ),
            provider_circuit_failure_threshold=_integer("PROVIDER_CIRCUIT_FAILURE_THRESHOLD", 5, 1),
            provider_circuit_window_size=_integer("PROVIDER_CIRCUIT_WINDOW_SIZE", 20, 2),
            provider_circuit_failure_rate=_fraction("PROVIDER_CIRCUIT_FAILURE_RATE", 0.5),
            provider_circuit_reset_seconds=_number("PROVIDER_CIRCUIT_RESET_SECONDS", 30, 1),
            provider_max_concurrency=_integer("PROVIDER_MAX_CONCURRENCY", 4, 1),
            token_budget=_integer("LLM_TOKEN_BUDGET", 12_000, 256),
            token_route_lite_threshold=_integer("LLM_LITE_TOKEN_THRESHOLD", 8_000, 1),
            token_route_minimal_threshold=_integer("LLM_MINIMAL_TOKEN_THRESHOLD", 4_000, 1),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", "").strip(),
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", "").strip(),
            langfuse_base_url=(
                os.getenv("LANGFUSE_BASE_URL", "").strip() or os.getenv("LANGFUSE_HOST", "").strip()
            ),
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
        return (
            self.allow_fixture_fallback
            and self.developer_diagnostic_mode
            and self.app_env != "production"
            and not self.sandbox_mode
        )

    @property
    def recall_configuration_complete(self) -> bool:
        return bool(
            self.opensearch_url
            and self.ann_backend == "faiss"
            and self.ann_index_path
            and self.tower_query_endpoint
            and self.tower_item_endpoint
        )

    @property
    def recall_required_actions(self) -> tuple[str, ...]:
        actions: list[str] = []
        if not self.opensearch_url:
            actions.append("Configure OPENSEARCH_URL for category knowledge recall")
        if self.ann_backend == "disabled":
            actions.append("Enable ANN_BACKEND=faiss and configure ANN_INDEX_PATH for ANN recall")
        elif not self.ann_index_path:
            actions.append("Configure ANN_INDEX_PATH for Faiss recall")
        if not self.tower_query_endpoint:
            actions.append("Configure TOWER_QUERY_ENDPOINT for query-tower recall")
        if not self.tower_item_endpoint:
            actions.append("Configure TOWER_ITEM_ENDPOINT for item-tower recall")
        return tuple(actions)

    @property
    def data_mode(self) -> Literal["live", "sandbox", "mixed"]:
        if self.fixture_fallback_enabled:
            return "mixed"
        return "sandbox" if self.sandbox_mode else "live"

    @property
    def task_ready(self) -> bool:
        if self.active_agent_mode == "unavailable":
            return False
        if self.app_env == "production" and (
            self.sandbox_mode
            or self.allow_fixture_fallback
            or self.developer_diagnostic_mode
            or self.store_backend == "memory"
            or not self.auth_enabled
            or not self.rate_limit_enabled
        ):
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
        if self.app_env == "production" and self.developer_diagnostic_mode:
            actions.append("Disable DEVELOPER_DIAGNOSTIC_MODE in production")
        if self.allow_fixture_fallback and not self.developer_diagnostic_mode:
            actions.append("Enable DEVELOPER_DIAGNOSTIC_MODE to allow fixture fallback")
        if not self.enabled_marketplaces:
            actions.append(
                "Configure at least one data-provider marketplace channel endpoint/credential pair, "
                "or explicitly enable SANDBOX_MODE for local testing"
            )
        for marketplace in self.marketplaces:
            if marketplace.state == "partial":
                actions.append(
                    f"Complete both {marketplace.name.upper()}_DATA_CHANNEL_ENDPOINT and "
                    f"{marketplace.name.upper()}_DATA_CHANNEL_CREDENTIAL "
                    f"(legacy aliases {marketplace.name.upper()}_API_ENDPOINT and "
                    f"{marketplace.name.upper()}_API_KEY are supported)"
                )
        if self.app_env == "production" and self.store_backend == "memory":
            actions.append("Use STORE_BACKEND=redis for persistent production preferences")
        if self.app_env == "production" and not self.auth_enabled:
            actions.append(
                "Enable AUTH_ENABLED and configure a trusted identity gateway in production"
            )
        if self.app_env == "production" and not self.rate_limit_enabled:
            actions.append("Enable RATE_LIMIT_ENABLED with a shared gateway limiter in production")
        return tuple(actions)

    @property
    def status(self) -> RuntimeStatus:
        if not self.task_ready:
            return "not_ready"
        if (
            self.sandbox_mode
            or self.fixture_fallback_enabled
            or len(self.configured_marketplaces) < len(MARKETPLACES)
            or (self.app_env == "production" and self.store_backend == "memory")
            or not self.recall_configuration_complete
        ):
            return "degraded"
        return "ready"


def get_settings() -> Settings:
    return Settings.from_env()
