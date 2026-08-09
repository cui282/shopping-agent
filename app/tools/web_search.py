from __future__ import annotations

import os

import httpx

from app.config import get_settings
from app.provider_resilience import ProviderCircuitOpenError, get_provider_resilience
from app.schemas import ProviderMetadata, SearchResult, WebSearchOutput


async def web_search(query: str, max_results: int = 5) -> WebSearchOutput:
    """Search the public web through Tavily when it is configured."""

    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return WebSearchOutput(
            query=query,
            results=[],
            provider=ProviderMetadata(
                source="live",
                provider="tavily",
                status="unavailable",
                fallback_reason="TAVILY_API_KEY is not configured; no web results were fabricated",
            ),
        )

    async def request_provider() -> dict[str, object]:
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(
            timeout=get_settings().provider_timeout_seconds,
            transport=transport,
        ) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": api_key, "query": query, "max_results": min(max_results, 10)},
            )
            response.raise_for_status()
            return response.json()

    try:
        payload = await get_provider_resilience().execute("tavily", request_provider)
    except ProviderCircuitOpenError as exc:
        return WebSearchOutput(
            query=query,
            results=[],
            provider=ProviderMetadata(
                source="live",
                provider="tavily",
                status="unavailable",
                fallback_reason=str(exc),
                failure_reason="circuit_open",
            ),
        )
    except Exception as exc:  # noqa: BLE001 - web evidence is an optional provider
        return WebSearchOutput(
            query=query,
            results=[],
            provider=ProviderMetadata(
                source="live",
                provider="tavily",
                status="unavailable",
                fallback_reason=f"provider request failed: {type(exc).__name__}",
            ),
        )
    results = [
        SearchResult(
            title=str(item.get("title", "")), url=item.get("url"), snippet=item.get("content")
        )
        for item in payload.get("results", [])
        if item.get("title")
    ]
    return WebSearchOutput(
        query=query,
        results=results,
        provider=ProviderMetadata(source="live", provider="tavily"),
    )
