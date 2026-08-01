from __future__ import annotations

import json
import os
from typing import Any, Literal, Protocol

from app.config import get_settings
from app.schemas import PreferenceBackendStatus


class PreferenceStoreError(RuntimeError):
    """The configured preference backend cannot serve a request."""


class PreferenceStore(Protocol):
    @property
    def backend_status(self) -> PreferenceBackendStatus: ...

    async def get(self, user_id: str) -> dict[str, Any]: ...
    async def put(self, user_id: str, preferences: dict[str, Any]) -> None: ...
    async def delete(self, user_id: str) -> None: ...


class InMemoryPreferenceStore:
    def __init__(
        self,
        *,
        requested_backend: Literal["memory", "redis"] = "memory",
        fallback_reason: str | None = None,
    ) -> None:
        self._values: dict[str, dict[str, Any]] = {}
        self._backend_status = PreferenceBackendStatus(
            requested_backend=requested_backend,
            backend="memory",
            durability="local_evaluation",
            fallback_reason=fallback_reason,
        )

    @property
    def backend_status(self) -> PreferenceBackendStatus:
        return self._backend_status

    async def get(self, user_id: str) -> dict[str, Any]:
        return dict(self._values.get(user_id, {}))

    async def put(self, user_id: str, preferences: dict[str, Any]) -> None:
        self._values[user_id] = dict(preferences)

    async def delete(self, user_id: str) -> None:
        self._values.pop(user_id, None)


class RedisPreferenceStore:
    def __init__(self, url: str, ttl_seconds: int) -> None:
        try:
            from redis.asyncio import from_url
        except ImportError as exc:
            raise RuntimeError("install the production extra to use Redis") from exc
        self._client = from_url(url, decode_responses=True)
        self._ttl_seconds = ttl_seconds

    @property
    def backend_status(self) -> PreferenceBackendStatus:
        return PreferenceBackendStatus(
            requested_backend="redis",
            backend="redis",
            durability="durable",
        )

    @staticmethod
    def _key(user_id: str) -> str:
        return f"shopping-agent:preferences:{user_id}"

    async def get(self, user_id: str) -> dict[str, Any]:
        value = await self._client.get(self._key(user_id))
        return json.loads(value) if value else {}

    async def put(self, user_id: str, preferences: dict[str, Any]) -> None:
        await self._client.set(
            self._key(user_id),
            json.dumps(preferences, ensure_ascii=False),
            ex=self._ttl_seconds,
        )

    async def delete(self, user_id: str) -> None:
        await self._client.delete(self._key(user_id))


class ResilientPreferenceStore:
    """Use Redis when healthy and disclose a local-evaluation fallback outside production."""

    def __init__(self, primary: RedisPreferenceStore, *, production: bool) -> None:
        self._primary = primary
        self._fallback = InMemoryPreferenceStore(requested_backend="redis")
        self._production = production
        self._fallback_reason: str | None = None

    @property
    def backend_status(self) -> PreferenceBackendStatus:
        if self._fallback_reason is None:
            return self._primary.backend_status
        return self._fallback.backend_status.model_copy(
            update={"fallback_reason": self._fallback_reason}
        )

    def _fallback_or_raise(self, operation: str, exc: Exception) -> None:
        reason = f"Redis {operation} failed: {type(exc).__name__}"
        if self._production:
            raise PreferenceStoreError(reason) from exc
        self._fallback_reason = reason

    async def get(self, user_id: str) -> dict[str, Any]:
        try:
            return await self._primary.get(user_id)
        except Exception as exc:  # noqa: BLE001 - runtime backend failure is disclosed
            self._fallback_or_raise("get", exc)
            return await self._fallback.get(user_id)

    async def put(self, user_id: str, preferences: dict[str, Any]) -> None:
        try:
            await self._primary.put(user_id, preferences)
        except Exception as exc:  # noqa: BLE001 - runtime backend failure is disclosed
            self._fallback_or_raise("put", exc)
            await self._fallback.put(user_id, preferences)

    async def delete(self, user_id: str) -> None:
        try:
            await self._primary.delete(user_id)
        except Exception as exc:  # noqa: BLE001 - runtime backend failure is disclosed
            self._fallback_or_raise("delete", exc)
            await self._fallback.delete(user_id)


def build_preference_store() -> PreferenceStore:
    settings = get_settings()
    if settings.store_backend == "redis":
        try:
            primary = RedisPreferenceStore(
                os.getenv("STORE_REDIS_URL", "redis://localhost:6379/2"),
                settings.preference_ttl_seconds,
            )
        except Exception as exc:
            reason = f"Redis backend unavailable: {type(exc).__name__}"
            if settings.app_env == "production":
                raise PreferenceStoreError(reason) from exc
            return InMemoryPreferenceStore(
                requested_backend="redis",
                fallback_reason=reason,
            )
        return ResilientPreferenceStore(primary, production=settings.app_env == "production")
    return InMemoryPreferenceStore()
