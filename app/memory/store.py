from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Literal, Protocol

from app.config import get_settings
from app.memory.relevance import rank_relevant_preferences
from app.schemas import PreferenceBackendStatus


class PreferenceStoreError(RuntimeError):
    """The configured preference backend cannot serve a request."""


class PreferenceStore(Protocol):
    @property
    def backend_status(self) -> PreferenceBackendStatus: ...

    async def get(self, user_id: str) -> dict[str, Any]: ...
    async def read_relevant(
        self, user_id: str, query: str, *, limit: int = 5
    ) -> dict[str, Any]: ...
    async def put(self, user_id: str, preferences: dict[str, Any]) -> None: ...
    async def delete(self, user_id: str) -> None: ...


class InMemoryPreferenceStore:
    def __init__(
        self,
        *,
        requested_backend: Literal["memory", "redis", "postgres"] = "memory",
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

    async def read_relevant(self, user_id: str, query: str, *, limit: int = 5) -> dict[str, Any]:
        return rank_relevant_preferences(self._values.get(user_id, {}), query, limit=limit)

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

    async def read_relevant(self, user_id: str, query: str, *, limit: int = 5) -> dict[str, Any]:
        return rank_relevant_preferences(await self.get(user_id), query, limit=limit)

    async def put(self, user_id: str, preferences: dict[str, Any]) -> None:
        await self._client.set(
            self._key(user_id),
            json.dumps(preferences, ensure_ascii=False),
            ex=self._ttl_seconds,
        )

    async def delete(self, user_id: str) -> None:
        await self._client.delete(self._key(user_id))


_POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS shopping_agent_preferences (
    user_id VARCHAR(120) PRIMARY KEY,
    preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS shopping_agent_preferences_expires_at_idx
    ON shopping_agent_preferences (expires_at);
"""


class PostgresPreferenceStore:
    """Durable structured preference storage backed by PostgreSQL JSONB."""

    def __init__(self, url: str, ttl_seconds: int) -> None:
        self._url = url
        self._ttl_seconds = ttl_seconds
        self._pool: Any = None
        self._schema_initialized = False
        self._pool_lock = asyncio.Lock()
        self._schema_lock = asyncio.Lock()

    @property
    def backend_status(self) -> PreferenceBackendStatus:
        return PreferenceBackendStatus(
            requested_backend="postgres",
            backend="postgres",
            durability="durable",
        )

    async def _get_pool(self) -> Any:
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is not None:
                return self._pool
            try:
                import asyncpg
            except ImportError as exc:
                raise RuntimeError("install project dependencies to use PostgreSQL") from exc
            self._pool = await asyncpg.create_pool(
                self._url,
                min_size=1,
                max_size=10,
                command_timeout=10,
            )
        return self._pool

    async def _ensure_schema(self) -> Any:
        pool = await self._get_pool()
        if not self._schema_initialized:
            async with self._schema_lock:
                if not self._schema_initialized:
                    await pool.execute(_POSTGRES_SCHEMA)
                    self._schema_initialized = True
        return pool

    async def get(self, user_id: str) -> dict[str, Any]:
        pool = await self._ensure_schema()
        row = await pool.fetchrow(
            """
            SELECT preferences
            FROM shopping_agent_preferences
            WHERE user_id = $1
              AND (expires_at IS NULL OR expires_at > NOW())
            """,
            user_id,
        )
        if row is None:
            return {}
        value = row["preferences"]
        if isinstance(value, str):
            value = json.loads(value)
        return dict(value) if isinstance(value, dict) else {}

    async def read_relevant(self, user_id: str, query: str, *, limit: int = 5) -> dict[str, Any]:
        return rank_relevant_preferences(await self.get(user_id), query, limit=limit)

    async def put(self, user_id: str, preferences: dict[str, Any]) -> None:
        pool = await self._ensure_schema()
        await pool.execute(
            """
            INSERT INTO shopping_agent_preferences (user_id, preferences, updated_at, expires_at)
            VALUES ($1, $2::jsonb, NOW(), NOW() + ($3::int * INTERVAL '1 second'))
            ON CONFLICT (user_id) DO UPDATE SET
                preferences = EXCLUDED.preferences,
                updated_at = EXCLUDED.updated_at,
                expires_at = EXCLUDED.expires_at
            """,
            user_id,
            json.dumps(preferences, ensure_ascii=False),
            self._ttl_seconds,
        )

    async def delete(self, user_id: str) -> None:
        pool = await self._ensure_schema()
        await pool.execute(
            "DELETE FROM shopping_agent_preferences WHERE user_id = $1",
            user_id,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._schema_initialized = False


class ResilientPreferenceStore:
    """Use the selected durable backend and disclose a local fallback outside production."""

    def __init__(
        self,
        primary: RedisPreferenceStore | PostgresPreferenceStore,
        *,
        requested_backend: Literal["redis", "postgres"],
        production: bool,
    ) -> None:
        self._primary = primary
        self._requested_backend = requested_backend
        self._fallback = InMemoryPreferenceStore(requested_backend=requested_backend)
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
        backend_label = "PostgreSQL" if self._requested_backend == "postgres" else "Redis"
        reason = f"{backend_label} {operation} failed: {type(exc).__name__}"
        if self._production:
            raise PreferenceStoreError(reason) from exc
        self._fallback_reason = reason

    async def get(self, user_id: str) -> dict[str, Any]:
        try:
            return await self._primary.get(user_id)
        except Exception as exc:  # noqa: BLE001 - runtime backend failure is disclosed
            self._fallback_or_raise("get", exc)
            return await self._fallback.get(user_id)

    async def read_relevant(self, user_id: str, query: str, *, limit: int = 5) -> dict[str, Any]:
        try:
            return await self._primary.read_relevant(user_id, query, limit=limit)
        except Exception as exc:  # noqa: BLE001 - runtime backend failure is disclosed
            self._fallback_or_raise("read_relevant", exc)
            return await self._fallback.read_relevant(user_id, query, limit=limit)

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

    async def close(self) -> None:
        close = getattr(self._primary, "close", None)
        if callable(close):
            await close()


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
        return ResilientPreferenceStore(
            primary,
            requested_backend="redis",
            production=settings.app_env == "production",
        )
    if settings.store_backend == "postgres":
        primary = PostgresPreferenceStore(
            settings.store_postgres_url,
            settings.preference_ttl_seconds,
        )
        return ResilientPreferenceStore(
            primary,
            requested_backend="postgres",
            production=settings.app_env == "production",
        )
    return InMemoryPreferenceStore()
