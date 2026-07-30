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

- `query`: 1 to 4000 characters.
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

The frontend then connects to `WS /ws/{thread_id}`. Server messages use this envelope:

```json
{
  "type": "monitor_event",
  "event": "tool_end",
  "message": "item_search 工具调用完成",
  "data": {
    "tool_name": "item_search",
    "duration_ms": 184,
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
| `tool_end` | A typed tool completed, including duration and provider metadata when available |
| `fork` | A platform branch changed state |
| `task_result` | Terminal success; `data` is `ShoppingSummaryOutput` |
| `task_cancelled` | Terminal cancellation |
| `error` | Terminal failure; `data.code` is stable for client handling |

Clients must branch on `event`; `message` is display text and may change. Events generated before the WebSocket connects are buffered and replayed in order.

`GET /api/task/{thread_id}` returns a durable snapshot:

```json
{
  "thread_id": "thread-7b8cb4a9c23f",
  "status": "completed",
  "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
  "user_id": "browser-7f3c1f7a",
  "created_at": "2026-07-30T12:00:00Z",
  "updated_at": "2026-07-30T12:00:03Z",
  "result": {},
  "error_code": null,
  "error": null
}
```

Snapshot status is `running`, `completed`, `cancelled`, or `error`. Clients should discard events from a previously selected task when loading a snapshot.
If a process restart leaves a `running` snapshot without an owning worker, the next read atomically changes it to `error` with `error_code=task_interrupted` instead of presenting a permanently running task.

Stable task error codes include `providers_unavailable`, `fx_rates_unavailable`,
`task_timeout`, `task_failed`, and `task_interrupted`. `fx_rates_unavailable` means every
candidate uses a currency absent from `FX_RATES_JSON` and the built-in reference table.
If at least one candidate can be converted, candidates with missing rates are excluded and
`calculation_notice` discloses that partial exclusion.

`POST /api/task/{thread_id}/cancel` is idempotent for known terminal tasks. Active tasks return:

```json
{"status": "cancelled", "thread_id": "thread-7b8cb4a9c23f"}
```

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

Every recommendation includes `item_id`, `platform`, `title`, `image_url`, `product_url`, original price and currency, normalized `price_cny`, `shipping_cny`, `duty_cny`, `landed_cny`, `eta_days`, `rating`, `sales`, `attributes`, `source`, `reason`, and `rank`. Unknown optional values are `null`, never fabricated by a live adapter.

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

`SANDBOX_MODE=true` enables deterministic fixture data for local evaluation. Production rejects both sandbox mode and fixture fallback. In live mode, each marketplace is enabled only when both its endpoint and key are present. Missing or failed gateways never become live results; outside production, fixture fallback requires the separate `ALLOW_FIXTURE_FALLBACK=true` setting and is always disclosed.

`AGENT_MODE=auto` selects the model-assisted advisory step when model credentials are complete and otherwise uses rules. `AGENT_MODE=llm` with no credentials is unavailable unless `ALLOW_RULES_FALLBACK=true`.

## HTTP conventions

- JSON responses include `X-Request-ID`.
- Validation errors use FastAPI HTTP 422 responses.
- Stable application failures use `detail.code` or WebSocket `data.code`.
- External URLs are untrusted data; clients must allow only `http:` and `https:` before rendering links.
