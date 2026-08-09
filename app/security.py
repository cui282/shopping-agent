from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.config import get_settings

_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")

_DANGEROUS_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions?", re.IGNORECASE),
    re.compile(r"忽略.{0,10}(之前|以上|所有).{0,10}(指令|指示|规则)"),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"扮演.{0,10}角色"),
    re.compile(r"output\s+(all|every)\s+(user|system)", re.IGNORECASE),
    re.compile(r"reveal\s+(your|the)\s+(api|secret|key)", re.IGNORECASE),
)
_SENSITIVE_OUTPUT_PATTERNS = (
    re.compile(r"item_id\s*[:=]\s*\w+", re.IGNORECASE),
    re.compile(r"thread_id\s*[:=]\s*[\w-]+", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"https?://(?:vllm|reranker|opensearch):\d+", re.IGNORECASE),
    re.compile(r"\b(?:dispatch_tool|task_tool)\b"),
)
_SENSITIVE_LOG_KEYS = re.compile(
    r"(?:api[_-]?key|authorization|credential|password|secret|token)", re.IGNORECASE
)
_USER_ID_LOG_KEYS = re.compile(r"(?:^|_)(?:user|tenant)_id$", re.IGNORECASE)


class AuthenticationError(ValueError):
    """Raised when a trusted identity gateway did not provide a valid principal."""


class ToolCallDenied(ValueError):
    """Raised when a model attempts to invoke a tool outside the registered allow-list."""


def _allowed_tool_names() -> frozenset[str]:
    # Lazy import keeps the security module independent from the tool registry import graph.
    from app.agent.tool_registry import FULL_TOOL_SET

    return frozenset(spec.name for spec in FULL_TOOL_SET)


def validate_tool_call(tool_name: str) -> bool:
    """Return whether a tool name belongs to the typed application registry."""

    return bool(tool_name and tool_name in _allowed_tool_names())


def pre_tool_check(tool_call: Mapping[str, Any]) -> dict[str, str] | None:
    """Validate a model-shaped tool call before it reaches an execution boundary."""

    tool_name = str(tool_call.get("name", "")).strip()
    if validate_tool_call(tool_name):
        return None
    return {
        "error": f"工具 {tool_name or 'unknown'} 不在白名单内，拒绝执行。",
        "tool_call_id": str(tool_call.get("id", "unknown")),
    }


def sanitize_tool_output(text: str) -> str:
    """Replace instruction-like content returned by an untrusted provider or RAG source."""

    sanitized = text
    for pattern in _DANGEROUS_PATTERNS:
        sanitized = pattern.sub("[内容已过滤：疑似注入]", sanitized)
    return sanitized


def audit_output(text: str) -> tuple[bool, str]:
    """Redact internal identifiers and credentials before text is shown to a shopper."""

    audited = text
    violations = False
    for pattern in _SENSITIVE_OUTPUT_PATTERNS:
        if pattern.search(audited):
            violations = True
            audited = pattern.sub("[内部信息已隐藏]", audited)
    return not violations, audited


def sanitize_log_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Redact credentials and pseudonymize user/tenant identifiers in structured logs."""

    def sanitize(key: str, value: Any) -> Any:
        if _SENSITIVE_LOG_KEYS.search(key):
            return "[REDACTED]"
        if _USER_ID_LOG_KEYS.search(key) and isinstance(value, str):
            return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        if isinstance(value, Mapping):
            return {
                str(child_key): sanitize(str(child_key), child_value)
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [sanitize(key, item) for item in value]
        return value

    return {str(key): sanitize(str(key), value) for key, value in fields.items()}


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    tenant_id: str = "default"


def _valid_identity(value: str | None) -> bool:
    return bool(value and _IDENTITY_PATTERN.fullmatch(value))


def _signature_payload(user_id: str, tenant_id: str) -> bytes:
    return f"{tenant_id}:{user_id}".encode()


def principal_from_headers(headers: Mapping[str, str]) -> Principal | None:
    """Resolve the identity asserted by the deployment's trusted gateway.

    The application deliberately does not parse JWTs. In production, an identity gateway must
    validate the token and inject the configured user and tenant headers. An optional HMAC lets
    deployments authenticate those injected headers across an untrusted network boundary.
    """

    settings = get_settings()
    if not settings.auth_enabled:
        return None
    user_id = headers.get(settings.auth_user_header, "").strip()
    tenant_id = headers.get(settings.auth_tenant_header, "default").strip() or "default"
    if not _valid_identity(user_id) or not _valid_identity(tenant_id):
        raise AuthenticationError("authenticated user and tenant headers are required")
    if settings.auth_shared_secret:
        provided = headers.get(settings.auth_signature_header, "").strip().lower()
        expected = hmac.new(
            settings.auth_shared_secret.encode("utf-8"),
            _signature_payload(user_id, tenant_id),
            hashlib.sha256,
        ).hexdigest()
        if not provided or not hmac.compare_digest(provided, expected):
            raise AuthenticationError("identity signature is invalid")
    return Principal(user_id=user_id, tenant_id=tenant_id)


class SlidingWindowRateLimiter:
    """Small process-local limiter for a single worker.

    A multi-worker deployment should point the gateway at a shared limiter or replace this
    implementation with Redis. The in-process limiter still prevents accidental overload and is
    deterministic in local/test environments.
    """

    def __init__(self) -> None:
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, key: str, *, limit: int, window_seconds: float) -> tuple[bool, float]:
        now = monotonic()
        start, count = self._windows.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        if count >= limit:
            return False, max(0.0, window_seconds - (now - start))
        self._windows[key] = (start, count + 1)
        return True, max(0.0, window_seconds - (now - start))

    def clear(self) -> None:
        self._windows.clear()


rate_limiter = SlidingWindowRateLimiter()


def rate_limit_key(
    *,
    principal: Principal | None,
    client_host: str | None,
) -> str:
    if principal is not None:
        return f"tenant:{principal.tenant_id}:user:{principal.user_id}"
    return f"ip:{client_host or 'unknown'}"


__all__ = [
    "AuthenticationError",
    "Principal",
    "SlidingWindowRateLimiter",
    "ToolCallDenied",
    "audit_output",
    "pre_tool_check",
    "principal_from_headers",
    "rate_limit_key",
    "rate_limiter",
    "sanitize_log_fields",
    "sanitize_tool_output",
    "validate_tool_call",
]
