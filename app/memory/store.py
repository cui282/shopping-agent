from __future__ import annotations

import json
import os
from typing import Any, Protocol

from app.config import get_settings


class PreferenceStore(Protocol):
    async def get(self, user_id: str) -> dict[str, Any]: ...
    async def put(self, user_id: str, preferences: dict[str, Any]) -> None: ...
    async def delete(self, user_id: str) -> None: ...


class InMemoryPreferenceStore:
    def __init__(self) -> None:
        self._values: dict[str, dict[str, Any]] = {}

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


def build_preference_store() -> PreferenceStore:
    settings = get_settings()
    if settings.store_backend == "redis":
        return RedisPreferenceStore(
            os.getenv("STORE_REDIS_URL", "redis://localhost:6379/2"),
            settings.preference_ttl_seconds,
        )
    return InMemoryPreferenceStore()
