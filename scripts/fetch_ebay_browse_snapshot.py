"""Fetch a bounded eBay Browse API snapshot for offline indexing.

This targeted collector is for deployments without Feed API access. It uses explicit search terms,
filters for delivery to the configured Chinese destination, and stores only normalized runtime
records. It never persists an OAuth access token.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from app.data.ebay_browse import browse_context_header, browse_item_to_record, delivery_filter
from app.tools.fx_reference import fetch_conversion

load_dotenv()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", default=[], help="Browse keyword; repeatable")
    parser.add_argument(
        "--query-file", type=Path, help="newline-delimited keyword file; comments start with #"
    )
    parser.add_argument("--category-id", action="append", default=[], help="eBay category ID")
    parser.add_argument("--marketplace-id", default=os.getenv("EBAY_MARKETPLACE_ID", "EBAY_US"))
    parser.add_argument(
        "--destination-country", default=os.getenv("EBAY_DESTINATION_COUNTRY", "CN")
    )
    parser.add_argument(
        "--destination-postal-code",
        default=os.getenv("EBAY_DESTINATION_POSTAL_CODE", "100000"),
    )
    parser.add_argument(
        "--environment",
        choices=("sandbox", "production"),
        default=os.getenv("EBAY_ENV", "production"),
    )
    parser.add_argument("--limit", type=int, default=100, help="items per Browse request (max 200)")
    parser.add_argument("--pages", type=int, default=1, help="pages per query")
    parser.add_argument("--output", type=Path, default=Path("data/ebay_snapshots/browse.jsonl"))
    parser.add_argument(
        "--append",
        action="store_true",
        help="merge non-expired records already present at --output",
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/ebay_snapshots/browse-manifest.json")
    )
    parser.add_argument("--timeout", type=float, default=30)
    return parser.parse_args()


def _base_url(environment: str) -> str:
    return "https://api.sandbox.ebay.com" if environment == "sandbox" else "https://api.ebay.com"


def _credentials() -> tuple[str, str]:
    client_id = os.getenv("EBAY_CLIENT_ID", "").strip()
    client_secret = os.getenv("EBAY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            "EBAY_CLIENT_ID and EBAY_CLIENT_SECRET must be configured in the environment"
        )
    return client_id, client_secret


def _application_token(client: httpx.Client, environment: str, scope: str) -> str:
    client_id, client_secret = _credentials()
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = client.post(
        f"{_base_url(environment)}/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials", "scope": scope},
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise SystemExit("eBay OAuth response did not contain an access token")
    return token


def _queries(args: argparse.Namespace) -> list[str]:
    queries = list(args.query)
    if args.query_file:
        if not args.query_file.is_file():
            raise SystemExit(f"query file does not exist: {args.query_file}")
        queries.extend(
            line.strip()
            for line in args.query_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )
    queries = list(dict.fromkeys(query for query in queries if query.strip()))
    if not queries:
        raise SystemExit("provide --query or --query-file with at least one keyword")
    return queries


def fetch_snapshot(args: argparse.Namespace, client: httpx.Client) -> list[dict[str, Any]]:
    scope = os.getenv("EBAY_OAUTH_SCOPE", "https://api.ebay.com/oauth/api_scope").strip()
    token = _application_token(client, args.environment, scope)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    snapshot_at = now.isoformat().replace("+00:00", "Z")
    expires_at = (now + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    endpoint = f"{_base_url(args.environment)}/buy/browse/v1/item_summary/search"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-EBAY-C-MARKETPLACE-ID": args.marketplace_id,
        "X-EBAY-C-ENDUSERCTX": browse_context_header(
            args.destination_country, args.destination_postal_code
        ),
        "Accept-Language": "zh-CN",
    }
    queries = _queries(args)
    raw_items: dict[str, dict[str, Any]] = {}
    for query in queries:
        for page in range(args.pages):
            params: dict[str, Any] = {
                "q": query,
                "limit": args.limit,
                "offset": page * args.limit,
                "filter": delivery_filter(args.destination_country, args.destination_postal_code),
            }
            if args.category_id:
                params["category_ids"] = ",".join(args.category_id)
            response = client.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("itemSummaries", []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("itemId")
                if isinstance(item_id, str) and item_id.strip():
                    raw_items[item_id.strip()] = item
            if len(items) < args.limit:
                break

    currencies = {
        str((item.get("price") or {}).get("currency") or "").upper()
        for item in raw_items.values()
        if isinstance(item.get("price"), dict)
    }
    conversions = {}
    for currency in currencies:
        try:
            conversion = fetch_conversion(currency, timeout=args.timeout)
        except httpx.HTTPError:
            conversion = None
        if conversion is not None:
            conversions[currency] = conversion

    records: dict[str, dict[str, Any]] = {}
    if args.append and args.output.is_file():
        now_utc = datetime.now(timezone.utc)
        for line in args.output.read_text(encoding="utf-8").splitlines():
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(existing, dict):
                continue
            expires = existing.get("expires_at")
            try:
                expires_at_value = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
            except ValueError:
                continue
            if expires_at_value > now_utc and isinstance(existing.get("item_id"), str):
                records[existing["item_id"]] = existing
    for item in raw_items.values():
        record = browse_item_to_record(
            item,
            marketplace_id=args.marketplace_id,
            snapshot_at=snapshot_at,
            expires_at=expires_at,
            destination_country=args.destination_country,
            destination_postal_code=args.destination_postal_code,
            conversions=conversions,
            provider="ebay-buy-browse-snapshot",
        )
        if record is not None:
            records[record["item_id"]] = record
    return list(records.values())


def main() -> int:
    args = _args()
    if not 1 <= args.limit <= 200 or args.pages < 1 or args.timeout <= 0:
        raise SystemExit(
            "limit must be 1..200, pages must be positive, and timeout must be positive"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=args.timeout) as client:
        records = fetch_snapshot(args, client)
    if not records:
        raise SystemExit("Browse API returned no indexable items")
    with args.output.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
    manifest = {
        "source": "ebay-buy-browse",
        "environment": args.environment,
        "marketplace_id": args.marketplace_id,
        "destination_country": args.destination_country,
        "destination_postal_code": args.destination_postal_code,
        "queries": _queries(args),
        "category_ids": args.category_id,
        "item_count": len(records),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output": str(args.output),
    }
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
