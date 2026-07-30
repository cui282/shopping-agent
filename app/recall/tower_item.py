from __future__ import annotations

import os

import httpx


async def encode_item(item: dict[str, object]) -> list[float]:
    endpoint = os.getenv("TOWER_ITEM_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("TOWER_ITEM_ENDPOINT is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(endpoint, json={"item": item})
        response.raise_for_status()
    return [float(value) for value in response.json()["embedding"]]
