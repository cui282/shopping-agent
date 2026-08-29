"""Direct eBay Browse fallback for queries missing from the offline product index."""

from __future__ import annotations

import asyncio
import base64
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.data.ebay_browse import browse_context_header, browse_item_to_record, delivery_filter
from app.data.product_document import candidate_from_product_record, normalize_product_record
from app.schemas import Candidate
from app.tools.fx_reference import fetch_conversion


class EbayBrowseError(RuntimeError):
    """The direct eBay Browse fallback cannot serve a query."""


_token_lock = asyncio.Lock()
_token: str | None = None
_token_expires_at: datetime | None = None


def configured() -> bool:
    return bool(
        os.getenv("EBAY_CLIENT_ID", "").strip() and os.getenv("EBAY_CLIENT_SECRET", "").strip()
    )


def _base_url() -> str:
    environment = os.getenv("EBAY_ENV", "production").strip().lower()
    return "https://api.sandbox.ebay.com" if environment == "sandbox" else "https://api.ebay.com"


async def _application_token(client: httpx.AsyncClient) -> str:
    global _token, _token_expires_at
    now = datetime.now(timezone.utc)
    if _token and _token_expires_at and _token_expires_at > now + timedelta(seconds=30):
        return _token
    async with _token_lock:
        now = datetime.now(timezone.utc)
        if _token and _token_expires_at and _token_expires_at > now + timedelta(seconds=30):
            return _token
        client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
        client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise EbayBrowseError("EBAY_CLIENT_ID and EBAY_CLIENT_SECRET are not configured")
        encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = await client.post(
            f"{_base_url()}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": os.getenv(
                    "EBAY_OAUTH_SCOPE", "https://api.ebay.com/oauth/api_scope"
                ).strip(),
            },
        )
        if response.status_code != 200:
            raise EbayBrowseError(f"eBay OAuth request failed: HTTP {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 7200)
        if not isinstance(token, str) or not token:
            raise EbayBrowseError("eBay OAuth response did not contain an access token")
        _token = token
        _token_expires_at = now + timedelta(seconds=max(60, int(expires_in)))
        return token


async def search(
    query: str,
    *,
    top_k: int = 20,
    destination_country: str | None = None,
    destination_postal_code: str | None = None,
) -> list[Candidate]:
    """Search eBay directly without writing or mutating the OpenSearch index."""

    if not configured():
        raise EbayBrowseError("eBay official credentials are not configured")
    country = (destination_country or os.getenv("EBAY_DESTINATION_COUNTRY", "CN")).strip().upper()
    postal_code = (
        destination_postal_code or os.getenv("EBAY_DESTINATION_POSTAL_CODE", "100000")
    ).strip()
    marketplace = os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US").strip()
    limit = max(1, min(top_k, 50))
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot_at = now.isoformat().replace("+00:00", "Z")
    expires_at = (now + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    async with httpx.AsyncClient(timeout=20) as client:
        token = await _application_token(client)
        response = await client.get(
            f"{_base_url()}/buy/browse/v1/item_summary/search",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "X-EBAY-C-MARKETPLACE-ID": marketplace,
                "X-EBAY-C-ENDUSERCTX": browse_context_header(country, postal_code),
                "Accept-Language": "zh-CN",
            },
            params={
                "q": query,
                "limit": limit,
                "filter": delivery_filter(country, postal_code),
            },
        )
        if response.status_code != 200:
            raise EbayBrowseError(f"eBay Browse request failed: HTTP {response.status_code}")
        payload = response.json()

    items = payload.get("itemSummaries", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return []
    currencies = {
        str((item.get("price") or {}).get("currency") or "").upper()
        for item in items
        if isinstance(item, dict) and isinstance(item.get("price"), dict)
    }
    conversions: dict[str, Any] = {}
    for currency in currencies:
        try:
            conversion = await asyncio.to_thread(fetch_conversion, currency, timeout=20)
        except httpx.HTTPError:
            conversion = None
        if conversion is not None:
            conversions[currency] = conversion
    candidates: list[Candidate] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        record = browse_item_to_record(
            item,
            marketplace_id=marketplace,
            snapshot_at=snapshot_at,
            expires_at=expires_at,
            destination_country=country,
            destination_postal_code=postal_code,
            conversions=conversions,
            provider="ebay-buy-browse-live",
        )
        if record is None:
            continue
        normalized = normalize_product_record(record, default_platform="ebay")
        if normalized is None:
            continue
        try:
            candidates.append(candidate_from_product_record(normalized))
        except (KeyError, TypeError, ValueError):
            continue
    return candidates[:limit]


def reset_token_cache() -> None:
    global _token, _token_expires_at
    _token = None
    _token_expires_at = None


__all__ = ["EbayBrowseError", "configured", "reset_token_cache", "search"]
