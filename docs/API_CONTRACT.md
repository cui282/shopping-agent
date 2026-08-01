# Shopping Agent frontend/backend contract

This document describes the HTTP and WebSocket contract for version `0.1.x`. Pydantic models in `app/schemas.py` are authoritative; matching frontend types live in `frontend/src/types/api.ts`.

## Service status

`GET /api/health` is a liveness probe. It does not promise that research tasks can run.

```json
{
  "status": "ok",
  "service": "shopping-agent",
  "version": "0.1.0"
}
```

`GET /api/readiness` reports the effective runtime and user-facing capabilities.

```json
{
  "status": "degraded",
  "task_ready": true,
  "environment": "development",
  "runtime_mode": "sandbox",
  "agent_mode": "rules",
  "requested_agent_mode": "rules",
  "preference_store": "memory",
  "providers": {
    "amazon": {"configured": false, "state": "missing"},
    "shopee": {"configured": false, "state": "missing"},
    "aliexpress": {"configured": false, "state": "missing"},
    "ebay": {"configured": false, "state": "missing"}
  },
  "capabilities": {
    "websocket_events": true,
    "persistent_snapshots": true,
    "image_upload": true,
    "image_analysis": false
  },
  "required_actions": []
}
```

`status` is `ready`, `degraded`, or `not_ready`. Clients must use `task_ready`, not liveness or provider count, to enable task submission.

## Task lifecycle

`POST /api/task` accepts:

```json
{
  "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
  "thread_id": null,
  "user_id": "browser-7f3c1f7a",
  "upload_ids": []
}
```

- `query`: 1 to 4000 characters after trimming leading and trailing whitespace; empty or
  whitespace-only values are rejected.
- `thread_id`: optional, 1 to 80 ASCII letters, digits, `_`, or `-`.
- `user_id`: required, 1 to 120 ASCII letters, digits, `_`, or `-`.
- `upload_ids`: at most 8 server-issued IDs. Upload does not imply image analysis support.

The service returns HTTP 202:

```json
{
  "status": "started",
  "thread_id": "thread-7b8cb4a9c23f"
}
```

When runtime configuration is incomplete, it returns HTTP 503:

```json
{
  "detail": {
    "code": "runtime_not_ready",
    "message": "Shopping Agent is not configured to run research tasks",
    "required_actions": ["Configure at least one marketplace endpoint/key pair"]
  }
}
```

The frontend then connects to `WS /ws/{thread_id}`. Every accepted connection first receives a
durable snapshot, even when the process-local event buffer is empty:

```json
{
  "type": "task_snapshot",
  "snapshot": {
    "thread_id": "thread-7b8cb4a9c23f",
    "run_id": "d41d8cd98f004204e9800998ecf8427e",
    "status": "running",
    "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
    "user_id": "browser-7f3c1f7a",
    "created_at": "2026-07-30T12:00:00Z",
    "updated_at": "2026-07-30T12:00:01Z",
    "events": [],
    "result": null,
    "error_code": null,
    "error": null
  },
  "timestamp": "2026-07-30T12:00:01Z"
}
```

The server then replays the snapshot's complete event timeline in `sequence` order and continues
with live events. Monitor events use this envelope:

```json
{
  "type": "monitor_event",
  "event_id": "evt-3df19cfce6dc49a7ba46844481608ef8",
  "thread_id": "thread-7b8cb4a9c23f",
  "run_id": "d41d8cd98f004204e9800998ecf8427e",
  "sequence": 12,
  "event": "tool_end",
  "message": "item_search 工具调用完成",
  "data": {
    "tool_name": "item_search",
    "duration_ms": 184,
    "outcome": "success",
    "source": "live",
    "provider": "amazon_api",
    "status": "ok",
    "fallback_reason": null
  },
  "timestamp": "2026-07-30T12:00:00Z"
}
```

Supported events:

| Event | Meaning |
| --- | --- |
| `session_created` | Worker accepted the task |
| `assistant_call` | Workflow state changed |
| `tool_start` | A typed tool started |
| `tool_end` | A typed tool completed, including duration, `outcome`, source, provider status, and fallback reason |
| `fork` | A marketplace branch started with explicit `platform` and `demand` |
| `task_result` | Terminal success; `data` is `ShoppingSummaryOutput` |
| `task_cancelled` | Terminal cancellation |
| `error` | Terminal failure; `data.code` is stable for client handling |

`tool_end.data.outcome` is `success`, `degraded`, or `failure`. A failed tool emits `tool_end`
before the task-level `error`. `fork.data` has this shape:

```json
{
  "sub_thread_id": "sub-a71c020f",
  "platform": "amazon",
  "demand": {
    "platform": "amazon",
    "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革"
  }
}
```

Clients must branch on `event`; `message` is display text and may change. `event_id` is stable for
the lifetime of the event. `run_id` identifies one execution of a thread, while `sequence` starts at
1 and increases monotonically within that run. Clients merge snapshot and live events only when
both `thread_id` and `run_id` match, deduplicate by `event_id`, sort by `sequence`, and never let a
late non-terminal event roll back a terminal task state. A WebSocket for an unknown or already
deleted task is rejected with close code `1008`.

After the bounded WebSocket reconnect budget is exhausted, clients poll this durable snapshot at
low frequency until it becomes terminal or the user selects another task. A still-running first
snapshot is not treated as the end of recovery.

`GET /api/task/{thread_id}` returns a durable snapshot:

```json
{
  "thread_id": "thread-7b8cb4a9c23f",
  "run_id": "d41d8cd98f004204e9800998ecf8427e",
  "status": "running",
  "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
  "user_id": "browser-7f3c1f7a",
  "created_at": "2026-07-30T12:00:00Z",
  "updated_at": "2026-07-30T12:00:03Z",
  "events": [
    {
      "type": "monitor_event",
      "event_id": "evt-3df19cfce6dc49a7ba46844481608ef8",
      "thread_id": "thread-7b8cb4a9c23f",
      "run_id": "d41d8cd98f004204e9800998ecf8427e",
      "sequence": 1,
      "event": "session_created",
      "message": "购物任务已创建",
      "data": {"thread_id": "thread-7b8cb4a9c23f", "reference_images": []},
      "timestamp": "2026-07-30T12:00:00Z"
    }
  ],
  "result": null,
  "error_code": null,
  "error": null
}
```

Snapshot status is `running`, `completed`, `cancelled`, or `error`. `events` is the complete,
untruncated activity history owned by the task run. Clients discard events from a different task or
`run_id`, but merge a reconnect snapshot with newer events already received for the same run.
Terminal status, result/error fields, and the terminal event are persisted together before
broadcast. A snapshot write failure aborts publication rather than exposing an event that cannot be
recovered. If persistence remains unavailable, the worker releases process-local ownership and
closes the active WebSocket with code `1011`; once storage is writable again, the next snapshot read
converts the last durable `running` state into `task_interrupted`.

If a process restart leaves a `running` snapshot without an owning worker, the next read atomically
changes it to `error` with `error_code=task_interrupted` and appends exactly one persistent `error`
event instead of presenting a permanently running task.

Stable task error codes include `providers_unavailable`, `fx_rates_unavailable`,
`task_timeout`, `task_failed`, and `task_interrupted`. `fx_rates_unavailable` means every
candidate uses a currency absent from `FX_RATES_JSON` and the built-in reference table.
If at least one candidate can be converted, candidates with missing rates are excluded and
`calculation_notice` discloses that partial exclusion.

`POST /api/task/{thread_id}/cancel` is idempotent for known terminal tasks. Active tasks return:

```json
{"status": "cancelled", "thread_id": "thread-7b8cb4a9c23f"}
```

Submitting a new request with an active `thread_id` replaces that run. Cancellation of the
superseded worker is internal and does not emit `task_cancelled`; the replacement gets a new
`run_id`, starts a fresh timeline at sequence 1, and owns the thread's single terminal event. The
old WebSocket is closed with code `1012`, after which reconnect receives the replacement snapshot
instead of mixing both runs.

`DELETE /api/task/{thread_id}` permanently removes a research task. An active worker is
cancelled and awaited before its durable snapshot, generated reports, WebSocket connection, and
buffered events are removed. The operation is idempotent for a valid thread ID so clients can
also clear stale local history entries:

```json
{"status": "deleted", "thread_id": "thread-7b8cb4a9c23f"}
```

Uploaded references and user preferences are not task-owned and are therefore not deleted.

## Result representation

`task_result.data` and `snapshot.result` contain:

```json
{
  "thread_id": "thread-7b8cb4a9c23f",
  "final_answer": "...",
  "recommendations": [],
  "comparison": [],
  "files": [
    {"name": "shopping-report.md", "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.md"}
  ],
  "provider_mode": "mixed",
  "providers": {
    "amazon": {
      "source": "live",
      "provider": "amazon_api",
      "status": "ok",
      "fallback_reason": null
    }
  },
  "calculation_notice": "汇率来源 ...；运费与税费为估算值。"
}
```

`provider_mode` is:

- `live`: all marketplace candidates came from configured live gateways.
- `mixed`: live and fixture candidates were combined by explicitly allowed fallback.
- `sandbox`: all marketplace candidates came from the explicit sandbox catalog.

Provider `source` is `live`, `curated`, `fixture`, or `computed`; status is `ok`, `degraded`, or `unavailable`.

Every recommendation and comparison row carries the same normalized offer evidence before adding
calculated price, shipping, duty, and ranking fields:

```json
{
  "item_id": "candidate-17c0f4d5d0f4b92f99f0",
  "platform": "ebay",
  "marketplace": "ebay",
  "offer_id": null,
  "title": "Acme X1 256 GB",
  "price": 129.99,
  "currency": "USD",
  "rating": null,
  "sales": null,
  "image_url": null,
  "product_url": "https://shop.example/search?q=acme+x1",
  "link_kind": "marketplace_search",
  "attributes": {},
  "identity": {
    "gtin": "4006381333931",
    "mpn": null,
    "brand": "Acme",
    "model": "X1"
  },
  "variant_attributes": {
    "capacity": "256 GB",
    "condition": "new"
  },
  "availability": "in_stock",
  "retrieved_at": "2026-07-30T10:00:00Z",
  "provenance": {
    "kind": "marketplace_gateway",
    "provider": "licensed-ebay-feed",
    "upstream_source": "ebay-buy-browse"
  },
  "source": "live"
}
```

`price` and `currency` are the original gateway or fixture amount and currency. `price_cny` is a
separate calculated field. `item_id` remains the stable internal candidate key for additive
compatibility; `offer_id` is the marketplace's actual offer identifier and remains `null` when the
gateway does not supply one. A generated `item_id` is never represented as offer Identity Evidence.
`marketplace` is the normalized contract name for the existing `platform` field and must equal it.

`identity` holds cross-platform identity evidence. Missing `gtin`, `mpn`, `brand`, and `model`
values are `null`. `variant_attributes` contains only supplied scalar attributes that distinguish
the Product Variant; an empty object means none were supplied. `attributes` is retained as a legacy
general-purpose field, but clients must not use it to invent missing identity.

`availability` and `retrieved_at` are nullable. Valid timezone-aware retrieval timestamps are
normalized to UTC; an old timestamp is preserved so the UI can disclose it, while an invalid,
timezone-less, or absent timestamp becomes `null`. The service does not replace it with the current
time. `provenance.kind` is `marketplace_gateway` or `sandbox_fixture`; `provider` identifies the
gateway/feed or deterministic fixture catalog and is `null` when the gateway does not supply a
trusted provider name. `upstream_source` is nullable. When both provenance values are unknown,
`provenance` itself is `null`.

`product_url` is retained for additive compatibility, while `link_kind` supplies its authoritative
meaning:

- `product_detail`: a concrete Product Detail Link supplied on that gateway offer.
- `marketplace_search`: a Marketplace Search Link supplied by a gateway or the sandbox catalog.
- `null`: no trusted, typed link is available; clients must omit the link.

Only absolute HTTP(S) URLs with a hostname and no embedded credentials are accepted. Unsafe or
malformed values become `null`; the service never constructs a detail URL from marketplace and
offer identifiers. Sandbox Results use only `marketplace_search`. A live result may also contain a
search link, and clients label links from `link_kind`, never by guessing from `source`.

Recommendation-only fields are `reason` and `rank`. Both recommendations and comparison rows also
include normalized `price_cny`, `shipping_cny`, `duty_cny`, `landed_cny`, `eta_days`, `duty_tier`,
and nullable estimation `note`. Unknown optional Product Evidence remains `null` and is never
filled by Agent Interpretation.

## Upload and files

`POST /api/upload` accepts one JPEG, PNG, or WebP file. The service validates MIME, binary signature, and the configured byte limit, then returns:

```json
{
  "upload_id": "55a4c40444124cc9a8527fd83d2e21c1",
  "name": "55a4c40444124cc9a8527fd83d2e21c1.jpg",
  "content_type": "image/jpeg",
  "size": 381204
}
```

The `image_analysis` readiness capability is currently false. Clients must hide or disable image-based research even though storage is available for future integrations.

`GET /api/files/{thread_id}/{name}` serves only report files listed in the completed result. Paths outside a task directory and internal task state files are rejected.

## Preferences

`GET /api/preferences/{user_id}` returns:

```json
{"user_id": "browser-7f3c1f7a", "preferences": {}}
```

`DELETE /api/preferences/{user_id}` removes the record. `memory` storage is process-local; `redis` uses `PREFERENCE_TTL_SECONDS`.

`user_id` is a storage partition key, not authentication. Public deployments must supply an authenticated identity at a trusted gateway and enforce ownership for tasks, preferences, WebSockets, and files.

## Provider boundary

`SANDBOX_MODE=true` enables deterministic fixture data for local end-to-end testing. Production rejects both sandbox mode and fixture fallback. In live mode, each marketplace is enabled only when both its endpoint and key are present. Missing or failed gateways never become live results; outside production, fixture fallback requires the separate `ALLOW_FIXTURE_FALLBACK=true` setting and is always disclosed.

`AGENT_MODE=auto` selects the model-assisted advisory step when model credentials are complete and otherwise uses rules. `AGENT_MODE=llm` with no credentials is unavailable unless `ALLOW_RULES_FALLBACK=true`.

### Marketplace Gateway search contract

Shopping Agent calls each configured gateway with `GET`, query parameters `query` and `top_k`, and
both `Authorization: Bearer <key>` and `X-API-Key: <key>` for compatibility. The Marketplace Gateway
adapter owns provider-specific OAuth or API-key exchange, request signing, region/site parameters,
pagination across upstream provider pages, rate limits, and mapping provider fields into this
normalized contract. Shopping Agent does not receive marketplace credentials and does not contain
Amazon-, eBay-, AliExpress-, or Shopee-specific signing logic.

The gateway response may be a top-level array, or an object using `items`, `products`, `results`,
`offers`, or `data`. `data` may itself be an array or an object using any of those collection keys.
For legacy compatibility, a collection wrapper may itself contain another supported collection
wrapper, such as `items: {"items": [...]}` or `products: {"products": [...]}`.
Provider, provenance, and retrieval-time metadata may appear on the top-level or nested `data`
wrapper; item-level values take precedence where supported.

Supported offer aliases are:

| Normalized field | Accepted gateway fields |
| --- | --- |
| `offer_id` | `offer_id`, `item_id`, `id`, `product_id`, `sku` |
| `title` | `title`, `name`, `product_name` |
| `price` | `price`, `current_price`, `sale_price` |
| `currency` | `currency`, `currency_code` |
| `rating` | `rating`, `score` |
| `sales` | `sales`, `sold`, `sales_count` |
| `image_url` | `image_url`, `image`, `thumbnail` |
| Product Detail Link | `product_url`, `detail_url`, `item_web_url`, `url`, `link` |
| Marketplace Search Link | `search_url`, `marketplace_search_url` |
| link kind | `link_kind`, `url_kind` |
| retrieval time | `retrieved_at`, `observed_at`, `fetched_at` |
| availability | `availability`, `availability_status`, `stock_status`, `in_stock` |
| GTIN | `identity.gtin/ean/upc/isbn` or the corresponding top-level field |
| MPN/brand/model | nested under `identity` or the corresponding top-level field |
| critical variant attributes | `identity.variant_attributes`, `identity.variant`, `variant_attributes`, `variant`, or legacy `attributes` |

Title, non-negative finite price, and currency are required for a usable offer. An item that
explicitly claims a marketplace different from the branch is rejected. Missing optional fields,
invalid optional numbers, unsafe URLs, and invalid timestamps are normalized to `null` rather than
failing the complete gateway response. Gateway output remains Product Evidence; the LLM boundary
cannot create or alter any of these fields.

## HTTP conventions

- JSON responses include `X-Request-ID`.
- Validation errors use FastAPI HTTP 422 responses.
- Stable application failures use `detail.code` or WebSocket `data.code`.
- External URLs are untrusted data; clients must allow only `http:` and `https:` before rendering links.
