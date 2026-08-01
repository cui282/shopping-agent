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
  "data_mode": "sandbox",
  "developer_diagnostic_mode": false,
  "agent_mode": "rules",
  "requested_agent_mode": "rules",
  "preference_store": "memory",
  "preference_backend": {
    "requested_backend": "memory",
    "backend": "memory",
    "durability": "local_evaluation",
    "fallback_reason": null
  },
  "providers": {
    "amazon": {"configured": false, "state": "missing", "available": true, "source": "fixture", "failure_reason": null},
    "shopee": {"configured": false, "state": "missing", "available": true, "source": "fixture", "failure_reason": null},
    "aliexpress": {"configured": false, "state": "missing", "available": true, "source": "fixture", "failure_reason": null},
    "ebay": {"configured": false, "state": "missing", "available": true, "source": "fixture", "failure_reason": null}
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
    "data_mode": "live",
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
| `tool_end` | A typed tool completed, including duration, `outcome`, source, provider status, failure reason, and fallback reason |
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
  },
  "data_mode": "live"
}
```

`assistant_call`, search `tool_start`, and `tool_end` data also carry `data_mode`.
`assistant_call.step` identifies the workflow phase and other diagnostic fields remain extensible.
`tool_end` carries nullable
`failure_reason` with one of `not_configured`, `request_failed`, `empty_response`, or
`sandbox_forbidden`; this code is stable for client handling while `fallback_reason` remains a
human-readable disclosure. Every event in one task uses the same data mode. `mixed` is emitted only
when explicit developer diagnostics are enabled.

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
  "data_mode": "live",
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
      "data": {"thread_id": "thread-7b8cb4a9c23f", "reference_images": [], "data_mode": "live"},
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
`unsupported_capability`, `task_timeout`, `task_failed`, and `task_interrupted`.
`unsupported_capability` is emitted before marketplace search when the requested destination is
not China mainland; China mainland is currently the only supported landed-cost destination.
`fx_rates_unavailable` means every candidate uses a currency absent from `FX_RATES_JSON` and the
built-in reference table. If at least one candidate can be converted, candidates with missing
rates are excluded and `calculation_notice` discloses that partial exclusion.
When `FX_RATES_JSON` is configured, `FX_RATES_AS_OF` is required so the response can preserve the
effective date of the configured rates; the calculation basis is always exposed alongside it.

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
  "mode": "exact_offer_comparison",
  "recommendations": [],
  "comparison": [],
  "matching_offers": [],
  "alternative_candidates": [],
  "files": [
    {"name": "shopping-report.md", "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.md"}
  ],
  "provider_mode": "mixed",
  "data_mode": "mixed",
  "result_kind": "partial",
  "match_status": "matched",
  "unavailable_marketplaces": ["ebay"],
  "working_assumptions": [
    {
      "code": "optional_color_unspecified",
      "field": "color",
      "value": "不设限",
      "reason": "请求未指定颜色，保留 Product Evidence 中可验证的各色候选。"
    }
  ],
  "unverified_candidates": [],
  "exclusions": [],
  "relaxation_suggestions": [],
  "exchange_rate": {
    "base_currency": "CNY",
    "source": "reference-table",
    "effective_date": "2026-01-01",
    "calculation_basis": "original_amount * rate_to_cny"
  },
  "calculation_exclusions": [],
  "ranking_profile": {
    "priority_order": ["landed_cost", "preference_match", "evidence_quality", "delivery_time"],
    "explicit": false
  },
  "preference_decisions": [
    {
      "field": "style_preferences",
      "value": "简约",
      "status": "applied",
      "source": "remembered_preference",
      "reason": "作为本任务的透明默认值参与 preference match ranking。"
    },
    {
      "field": "style_preferences",
      "value": "复古",
      "status": "overridden",
      "source": "remembered_preference",
      "reason": "当前请求存在冲突表达，Remembered Preference 不覆盖当前任务。"
    }
  ],
  "providers": {
    "amazon": {
      "source": "live",
      "provider": "amazon_api",
      "status": "ok",
      "fallback_reason": null,
      "failure_reason": null
    },
    "ebay": {
      "source": "live",
      "provider": "ebay_api",
      "status": "unavailable",
      "fallback_reason": "provider request failed: TimeoutException",
      "failure_reason": "request_failed"
    }
  },
  "calculation_notice": "比较货币：CNY；汇率来源：内置参考汇率表；effective date：2026-01-01；calculation basis：original_amount * rate_to_cny；运费、税费与时效均为估算；这不是 checkout guarantee。"
}
```

`data_mode` and the backwards-compatible `provider_mode` are:

- `live`: all marketplace candidates came from configured live gateways.
- `mixed`: live and fixture candidates were combined by explicitly allowed fallback.
- `sandbox`: all marketplace candidates came from the explicit sandbox catalog.

`result_kind` is `live`, `sandbox`, or `partial`. A `partial` result contains usable Product
Evidence from at least one enabled marketplace and lists every unavailable marketplace in
`unavailable_marketplaces` and `providers`. When all enabled marketplaces are unavailable, no
result is emitted and the terminal error code is `providers_unavailable`.

`match_status` is `matched` or `no_match` and is independent of `result_kind`: `no_match` is a
successful result with usable marketplace data but no candidate that is both fully verified and
compliant with every Hard Constraint. It must not be converted into a provider error. A `no_match`
result may contain `unverified_candidates` and `exclusions` for transparent follow-up.

`mode` is the normalized research intent and is one of:

- `product_research`: different Product Variants may be compared and ranked using the existing
  Hard Constraint and deterministic Ranking Profile rules. Every normalized offer is exposed in
  `matching_offers`/`comparison` with `identity_evidence.decision=not_required`.
- `exact_offer_comparison`: only offers that pass the deterministic identity matcher are exposed in
  `matching_offers`/`comparison` and can proceed to Hard Constraint eligibility and ranking. The
  normalized `ShoppingPlan` carries the same mode before marketplace search; the terminal result
  repeats it so clients never infer the mode from free text.

In `exact_offer_comparison`, identity eligibility is evaluated before Hard Constraints, landed-cost
ranking, or any lowest-price conclusion. `matching_offers` is the complete identity-proven set;
`recommendations` is the subset that also satisfies every Hard Constraint and is then ranked by the
deterministic `ranking_profile`. `alternative_candidates` is never included in `comparison`, formal
ranking, or minimum-price conclusions.

Provider `source` is `live`, `curated`, `fixture`, or `computed`; status is `ok`, `degraded`, or
`unavailable`. `failure_reason` is nullable and uses the stable provider failure codes above.

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

Every result offer also has `identity_evidence` (nullable on lower-level PricePoint values, and
present on result rows):

```json
{
  "decision": "matching_offer",
  "basis": "identifier",
  "matched_fields": ["identity.gtin", "capacity", "regional_version", "bundle", "condition"],
  "missing_fields": [],
  "conflicting_fields": [],
  "explanation": "identity.gtin 跨平台一致，且没有发现冲突的关键属性。"
}
```

The deterministic matcher first compares a normalized GTIN (including gateway UPC/EAN aliases) or
MPN when both offers provide the same cross-platform identifier. A conflicting material variant
attribute still rejects the match. If no authoritative identifier is present, brand and model plus
every material `variant_attributes` key supplied by either offer must be present and equal after
deterministic normalization; a missing or conflicting capacity, regional version, bundle, condition,
or other supplied variant attribute is insufficient. A marketplace-local `offer_id` or generated
`item_id` never proves cross-platform identity. Title similarity, image similarity, and LLM or image
analysis clues cannot create `Identity Evidence` or change eligibility.

An exact-mode row that fails identity eligibility is an `Alternative Candidate` with the same
normalized landed-cost fields plus `reason` and non-matching `identity_evidence`:

```json
{
  "item_id": "fixture-ebay-variant-2",
  "title": "Similar title, different regional version",
  "landed_cny": 704.20,
  "reason": "Alternative Candidate：关键 Product Variant 属性不一致，不能证明是同款。",
  "identity_evidence": {
    "decision": "alternative_candidate",
    "basis": "insufficient",
    "matched_fields": ["identity.brand", "identity.model"],
    "missing_fields": [],
    "conflicting_fields": ["regional_version"],
    "explanation": "关键 Product Variant 属性不一致，不能证明是同款。"
  }
}
```

`Recommendation.offer_kind` is `matching_offer` in exact mode and `research_candidate` in Product
Research. The UI and generated report expose the mode, identity evidence, matching offers, and
alternative candidates separately.

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
and nullable estimation `note`. `price` and `currency` preserve the original amount; all
comparison and ranking calculations use CNY. Every landed-cost row carries
`shipping_estimate`, `duty_estimate`, and `delivery_estimate`, each with `estimated`, `source`,
and `calculation_basis`. These are estimates, not checkout guarantees. Non-finite or negative
amounts are excluded with `calculation_exclusions[].reason_code=invalid_amount`; a currency with
no available CNY rate is excluded with `reason_code=unsupported_currency`. Excluded candidates
never enter eligibility or ranking.

Each Recommendation contains `constraint_evaluations`. Every evaluation has the normalized
`constraint`, a three-valued `status` (`satisfied`, `violated`, or `unknown`), a stable
`reason_code`, an explanation, and the Product Evidence or computed value supporting the result.
Only candidates whose evaluations are all `satisfied` can appear in `recommendations`; eligibility
is evaluated before ranking. Each Recommendation also contains a machine-readable
`score_breakdown` for the current `ranking_profile`, including landed-cost, preference-match,
evidence-quality, and delivery-time scores. `reason` is deterministic and may explain only Product
Evidence plus disclosed computed landed-cost and estimate fields; the LLM cannot create prices,
rates, evidence, eligibility, or ranking outcomes.

`ranking_profile.priority_order` is the request's effective order across `landed_cost`,
`preference_match`, `evidence_quality`, and `delivery_time`. When the request does not express a
priority, the default is landed cost first. The same profile and CNY values are present in the API
result, generated report, and UI disclosure. Ties use stable landed-cost, delivery-time, platform,
and `item_id` keys after the requested dimensions.

`unverified_candidates` uses the same normalized offer and landed-cost fields as a recommendation,
plus `reason` and `constraint_evaluations`. Any `unknown` evaluation sends the candidate to this
separate collection; it never participates in formal recommendations.

`exclusions` contains `item_id`, `platform`, `title`, `violated_count`, and
`violated_constraints`. Each violated constraint retains its machine-readable reason and evidence.
`working_assumptions` exposes optional defaults such as an unspecified colour or style. They are
visible assumptions, not Blocking Ambiguities or Hard Constraints. `relaxation_suggestions` only
describes possible changes and has `requires_confirmation=true`; the current task never applies a
relaxation automatically.

The normalized task intent represents budget, material, and specification Hard Constraints with
`id`, `kind`, `field`, `operator`, `value`, `unit`, and `label`. Chinese negative expressions such
as `不要塑料的` normalize to a `material` constraint with `operator=not_contains` and value `塑料`.
Remembered Preference is passed separately and remains a soft default; it cannot add a Hard
Constraint or override explicit current-task intent. The LLM may explain intent and results only;
Product Evidence, eligibility, and the deterministic outcome remain outside the LLM boundary.

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

The `image_analysis` readiness capability is currently false. Clients must hide or disable image-based research even though storage is available for future integrations. When readiness reports
`capabilities.image_analysis=true`, clients may show the reference-image input. Image analysis may
return an identity clue for display or query assistance, but it cannot populate authoritative
identifiers, create `Identity Evidence`, or make an offer a Matching Offer; the same deterministic
matcher remains the sole exact-mode eligibility gate.

`GET /api/files/{thread_id}/{name}` serves only report files listed in the completed result. Paths outside a task directory and internal task state files are rejected.

## Preferences

`GET /api/preferences/{user_id}` returns:

```json
{
  "user_id": "browser-7f3c1f7a",
  "preferences": {"style_preferences": ["简约"]},
  "backend": {
    "requested_backend": "memory",
    "backend": "memory",
    "durability": "local_evaluation",
    "fallback_reason": null
  }
}
```

`PUT /api/preferences/{user_id}` and `POST /api/preferences/{user_id}/commands` accept the same explicit
`MemoryCommand` body:

```json
{
  "action": "remember",
  "field": "style_preferences",
  "values": ["简约"],
  "scope": "future_tasks"
}
```

`action` is `remember` or `forget`; `field` is `material_preferences`, `style_preferences`,
`soft_preferences`, or `avoid`. The command boundary is deterministic. A task query is only
allowed to create a memory command when it contains explicit future scope such as `以后` or
`今后` plus a remember/forget verb. Ordinary search, current-task correction, task-result choice,
success, failure, and cancellation never update memory. New `remember` values apply to future
tasks, not the task that issued the command; explicit `forget` takes effect immediately. The
result's `preference_decisions` reports `applied`, `ignored`, and `overridden` values with their
source and reason. Remembered Preference can influence only the transparent `preference_match`
ranking dimension after Hard Constraint eligibility; it never creates or relaxes a Hard Constraint.

`DELETE /api/preferences/{user_id}` removes the whole record. `memory` storage is process-local and
marked `local_evaluation`, never durable. `redis` is the durable backend and uses
`PREFERENCE_TTL_SECONDS`. If Redis is unavailable outside production, the response and readiness
contract report `backend=memory`, `durability=local_evaluation`, and `fallback_reason`; production
fails closed instead of pretending the fallback is durable.

The delete response is also explicit about the backend used:

```json
{
  "status": "deleted",
  "user_id": "browser-7f3c1f7a",
  "backend": {
    "requested_backend": "memory",
    "backend": "memory",
    "durability": "local_evaluation",
    "fallback_reason": null
  }
}
```

`user_id` is an anonymous storage association key, not authentication, ownership, or authorization.
Public deployments must supply an authenticated identity at a trusted gateway and enforce ownership
for tasks, preferences, WebSockets, and files.

## Provider boundary

`SANDBOX_MODE=true` enables deterministic fixture data only for an explicit non-production sandbox
runtime. Production rejects sandbox mode and fixture fallback, and the provider boundary fails
closed even when called directly. In live mode, each marketplace is enabled only when both its
endpoint and key are present. Missing or failed gateways never become live results. Fixture
fallback is disabled unless both `ALLOW_FIXTURE_FALLBACK=true` and
`DEVELOPER_DIAGNOSTIC_MODE=true` are set outside production; such a task is explicitly marked
`data_mode=mixed` and is not a normal user result. A readiness provider has `available=true` with
`source=fixture` in sandbox, while missing live gateway configuration is always `available=false`.

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
