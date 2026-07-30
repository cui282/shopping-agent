from __future__ import annotations

import os

import httpx


async def encode_user(user_id: str, preferences: dict[str, object]) -> list[float]:
    endpoint = os.getenv("TOWER_USER_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("TOWER_USER_ENDPOINT is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            endpoint, json={"user_id": user_id, "preferences": preferences}
        )
        response.raise_for_status()
    return [float(value) for value in response.json()["embedding"]]
