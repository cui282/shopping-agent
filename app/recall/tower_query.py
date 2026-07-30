from __future__ import annotations

import os

import httpx


async def encode_query(query: str) -> list[float]:
    endpoint = os.getenv("TOWER_QUERY_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("TOWER_QUERY_ENDPOINT is not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(endpoint, json={"query": query})
        response.raise_for_status()
    return [float(value) for value in response.json()["embedding"]]
