from __future__ import annotations

import os

import httpx

from app.schemas import RememberedPreference


async def encode_user(user_id: str, preferences: RememberedPreference) -> list[float]:
    endpoint = os.getenv("TOWER_USER_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("TOWER_USER_ENDPOINT is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            endpoint,
            json={
                "anonymous_shopper_id": user_id,
                "remembered_preference": preferences.model_dump(mode="json"),
            },
        )
        response.raise_for_status()
    return [float(value) for value in response.json()["embedding"]]
