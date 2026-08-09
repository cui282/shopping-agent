from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic

from app.config import get_settings

_IDENTITY_PATTERN = re.compile(r"^[A-Za-z0-9_.:@-]{1,160}$")


class AuthenticationError(ValueError):
    """Raised when a trusted identity gateway did not provide a valid principal."""


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
    "principal_from_headers",
    "rate_limit_key",
    "rate_limiter",
]
