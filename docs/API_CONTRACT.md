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
  "recall": {
    "mode": "deterministic_fallback",
    "channels": {
      "opensearch": {
        "channel": "opensearch",
        "configured": false,
        "state": "unavailable",
        "reason_code": "not_configured",
        "reason": "channel is not configured; deterministic fallback remains active",
        "participated": false
      },
      "query_tower": {
        "channel": "query_tower",
        "configured": false,
        "state": "unavailable",
        "reason_code": "ann_backend_disabled",
        "reason": "ANN backend is disabled; deterministic fallback remains active",
        "participated": false
      },
      "item_tower": {
        "channel": "item_tower",
        "configured": false,
        "state": "unavailable",
        "reason_code": "ann_backend_disabled",
        "reason": "ANN backend is disabled; deterministic fallback remains active",
        "participated": false
      },
      "faiss": {
        "channel": "faiss",
        "configured": false,
        "state": "unavailable",
        "reason_code": "backend_disabled",
        "reason": "ANN_BACKEND is disabled; deterministic fallback remains active",
        "participated": false
      }
    },
    "required_actions": [
      "Configure OPENSEARCH_URL for category knowledge recall",
      "Enable ANN_BACKEND=faiss and configure ANN_INDEX_PATH for ANN recall",
      "Configure TOWER_QUERY_ENDPOINT for query-tower recall",
      "Configure TOWER_ITEM_ENDPOINT for item-tower recall"
    ],
    "personalization": {
      "configured": false,
      "state": "unavailable",
      "input_source": "none",
      "preference_fields": [],
      "preference_values": [],
      "signal": "none",
      "dimension": null,
      "matched_candidate_count": 0,
      "reason_code": "not_configured",
      "reason": "TOWER_USER_ENDPOINT is not configured; existing recall path remains active",
      "participated": false
    }
  },
  "required_actions": []
}
```

`status` is `ready`, `degraded`, or `not_ready`. Clients must use `task_ready`, not liveness or provider count, to enable task submission.
The additive `recall` object reports configuration before a task runs. Its `configured` state is
not a runtime health claim; terminal result provenance and `tool_end` carry the actual `ready`,
`degraded`, or `unavailable` state for each channel. Missing optional recall configuration keeps
the marketplace task runnable when the core runtime is ready, but readiness remains visibly
degraded and lists the required action.

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

When `thread_id` identifies a running or awaiting task, the new request replaces that active run.
A terminal Research Snapshot is immutable and cannot be replaced with the same `thread_id`; the
service returns HTTP 409 with `detail.code=thread_id_immutable`.

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
    "snapshot_id": "thread-7b8cb4a9c23f",
    "thread_id": "thread-7b8cb4a9c23f",
    "run_id": "d41d8cd98f004204e9800998ecf8427e",
    "generation": 0,
    "status": "running",
    "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
    "user_id": "browser-7f3c1f7a",
    "data_mode": "live",
    "resolved_query": null,
    "resolved_intent": null,
    "mode": null,
    "working_assumptions": [],
    "applied_preferences": {"material_preferences": [], "style_preferences": [], "soft_preferences": [], "avoid": []},
    "task_overrides": [],
    "constraint_relaxations": [],
    "provider_coverage": {},
    "product_evidence": [],
    "exchange_rate": null,
    "report_references": [],
    "created_at": "2026-07-30T12:00:00Z",
    "updated_at": "2026-07-30T12:00:01Z",
    "events": [],
    "result": null,
    "clarification": null,
    "clarification_answers": {},
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
    "fallback_reason": null,
    "failure_reason": null,
    "recall_provenance": null
  },
  "timestamp": "2026-07-30T12:00:00Z"
}
```

Supported events:

| Event | Meaning |
| --- | --- |
| `session_created` | Worker accepted the task |
| `assistant_call` | Workflow state changed |
| `context_compression` | The transient model-only context window was bounded; `data.status` is `applied`, `degraded`, or `not_needed`, and `data.reason_code` is stable. Counts and estimated tokens are disclosed without prompt text, sensitive messages, or configuration values. |
| `tool_start` | A typed tool started |
| `tool_end` | A typed tool completed, including duration, `outcome`, source, provider status, failure reason, and fallback reason |
| `fork` | A marketplace branch started with explicit `platform` and `demand` |
| `report_generated` | Deterministic Markdown, JSON, and PDF artifacts were generated from the completed Research Snapshot; `data.files` carries stable file IDs and content types |
| `intent_resolved` | The immutable task intent, Working Assumptions, applied Remembered Preference, Task Override, and any confirmed constraint changes were saved |
| `task_result` | Terminal success; `data` is `ShoppingSummaryOutput` |
| `task_cancelled` | Terminal cancellation |
| `clarification_required` | Non-terminal blocking ambiguity; `data` contains one `field`, stable `reason_code`, and one `question` |
| `clarification_resolved` | Non-terminal answer recorded for the same task; `data` contains the field, submitted response, and canonical `resolved_value` |
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
`context_compression.data` has this shape:

```json
{
  "status": "applied",
  "reason_code": "threshold_exceeded",
  "compressed_message_count": 8,
  "retained_message_count": 3,
  "estimated_tokens": 740,
  "summary_fields": [
    "resolved_hard_constraints",
    "product_variant",
    "exact_identity",
    "clarification_responses",
    "supported_destination",
    "working_assumptions",
    "remembered_preference",
    "task_overrides",
    "preference_sources"
  ],
  "data_mode": "sandbox"
}
```

The model-only context is derived afresh from typed task state and the durable event timeline.
It contains a structured summary plus recent messages, bounded by `COMPRESS_KEEP_RECENT` and
the deterministic character-based estimate configured by `COMPRESS_MAX_TOKENS`; it never becomes
the source of Product Evidence, recall inputs or provenance, eligibility, identity matching,
Landed Cost, ranking, reports, or snapshots. Compression is transient: the complete task events,
clarification responses, shopper decisions, snapshots, reports, and visible history remain
persisted and replayable. A degraded reason code means the recent-message fallback was used and
the task remains intact. Rebuilding a context after restart starts from durable typed state, so
repeated compression does not accumulate a semantic loss or re-ask a resolved Blocking Ambiguity.
`tool_end` carries nullable
`failure_reason` with one of `not_configured`, `request_failed`, `empty_response`, or
`sandbox_forbidden`; this code is stable for client handling while `fallback_reason` remains a
human-readable disclosure. Every event in one task uses the same data mode. `mixed` is emitted only
when explicit developer diagnostics are enabled.
The recall tool's `tool_end.data.recall_provenance` is the same immutable object persisted on the
terminal result and snapshot. It reports the actual recall mode, participating channels, candidate
counts, each channel's stable reason code, and the optional personalization report.

Clients must branch on `event`; `message` is display text and may change. `event_id` is stable for
the lifetime of the event. `run_id` identifies one execution of a thread, while `sequence` starts at
1 and increases monotonically within that run. Clients merge snapshot and live events only when
both `thread_id` and `run_id` match, deduplicate by `event_id`, sort by `sequence`, and never let a
late non-terminal event roll back a terminal task state. A WebSocket for an unknown or already
deleted task is rejected with close code `1008`.

After the bounded WebSocket reconnect budget is exhausted, clients poll this durable snapshot at
low frequency until it becomes terminal, enters `awaiting_clarification`, or the user selects
another task. A still-running first snapshot is not treated as the end of recovery.

`GET /api/task/{thread_id}` returns a durable snapshot:

```json
{
  "snapshot_id": "thread-7b8cb4a9c23f",
  "thread_id": "thread-7b8cb4a9c23f",
  "run_id": "d41d8cd98f004204e9800998ecf8427e",
  "generation": 0,
  "status": "running",
  "query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
  "user_id": "browser-7f3c1f7a",
  "data_mode": "live",
  "resolved_query": null,
  "resolved_intent": null,
  "mode": null,
  "working_assumptions": [],
  "applied_preferences": {"material_preferences": [], "style_preferences": [], "soft_preferences": [], "avoid": []},
  "task_overrides": [],
  "constraint_relaxations": [],
  "provider_coverage": {},
  "product_evidence": [],
  "exchange_rate": null,
  "report_references": [],
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
  "clarification": null,
  "clarification_answers": {},
  "error_code": null,
  "error": null,
  "recall_provenance": null
}
```

Snapshot status is `running`, `awaiting_clarification`, `completed`, `cancelled`, or `error`. `events` is the complete,
untruncated activity history owned by the task run. Clients discard events from a different task or
`run_id`, but merge a reconnect snapshot with newer events already received for the same run.
When status is `awaiting_clarification`, `clarification` contains the one pending question and
`clarification_answers` contains canonical answers already recorded for this task. This is a
non-terminal state: the task can be cancelled or deleted, and the same `thread_id`, `run_id`, and
event history continue after a valid answer.
Terminal status, result/error fields, and the terminal event are persisted together before
broadcast. A snapshot write failure aborts publication rather than exposing an event that cannot be
recovered. If persistence remains unavailable, the worker releases process-local ownership and
closes the active WebSocket with code `1011`; once storage is writable again, the next snapshot read
converts the last durable `running` state into `task_interrupted`.

If a process restart leaves an `awaiting_clarification` snapshot without an owning worker, it is
returned unchanged so the question can be answered. A `running` snapshot without an owning worker
is instead atomically changed to `error` with `error_code=task_interrupted` and appends exactly one persistent `error`
event instead of presenting a permanently running task.
An active worker holds the task's durable owner lock; a reader in another worker returns the running
snapshot unchanged while that lock is held and performs recovery only after ownership is available.

### Research Snapshot, Research Rerun, and Constraint Relaxation

Every completed task is also a `Research Snapshot`. `snapshot_id` is stable for the task and the
top-level fields preserve the resolved query and intent, research mode, Working Assumptions,
applied Remembered Preference, Task Override, provider coverage, complete Product Evidence with
each `retrieved_at`, exchange-rate provenance and effective date, report references, terminal
result, and the complete ordered event timeline. The data is written to the durable task boundary
before it is broadcast; reopening a completed snapshot is read-only and must not call a marketplace
gateway, preference recall, exchange-rate source, or recalculation path.

`GET /api/research?user_id=browser-7f3c1f7a` (also available as
`GET /api/research/snapshots?user_id=browser-7f3c1f7a`) returns
`{"snapshots": [TaskSnapshot, ...]}` from durable storage, ordered by most recently updated and
limited to the same Anonymous Shopper ID supplied by the caller. The ID is an association key, not
authentication or authorization; a hosted deployment must enforce ownership at a trusted gateway.
`GET /api/research/{thread_id}` opens one snapshot and has no side effects. The existing
`GET /api/task/{thread_id}` is the equivalent task-scoped read. Legacy snapshots missing the
newer additive fields are normalized only in the response; the stored bytes are not rewritten.

### Snapshot reports

Completed snapshots expose one typed `ResearchReportSnapshot` projection. Markdown, JSON, and PDF
are deterministic renderings of that same projection; report generation never calls a Marketplace
Gateway, preference recall, an LLM, or an exchange-rate source. The projection keeps
`snapshot_effective_at` and `lineage`, so reopening an older report does not describe it as current
market state.

The JSON download is a structured serialization of that projection (not a hand-built string). Its
root fields retain the terminal `ShoppingSummaryOutput` contract plus `report_schema_version`,
`snapshot_id`, `snapshot_effective_at`, `snapshot_created_at`, `snapshot_status`, `user_id`,
`query`, `lineage`, and typed `notices`. Markdown and PDF surface the same notices and result
evidence in human-readable sections.

`GET /api/task/{thread_id}/reports` and its alias `GET /api/reports/{thread_id}` list the files:

```json
{
  "status": "ready",
  "snapshot_id": "thread-7b8cb4a9c23f",
  "snapshot_effective_at": "2026-07-30T12:00:03Z",
  "files": [
    {
      "file_id": "thread-7b8cb4a9c23f:markdown",
      "format": "markdown",
      "name": "shopping-report.md",
      "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.md",
      "content_type": "text/markdown; charset=utf-8"
    },
    {
      "file_id": "thread-7b8cb4a9c23f:json",
      "format": "json",
      "name": "shopping-report.json",
      "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.json",
      "content_type": "application/json; charset=utf-8"
    },
    {
      "file_id": "thread-7b8cb4a9c23f:pdf",
      "format": "pdf",
      "name": "shopping-report.pdf",
      "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.pdf",
      "content_type": "application/pdf"
    }
  ]
}
```

`POST /api/task/{thread_id}/reports` is an idempotent report-generation command. It accepts no
shopping input, requires `status=completed`, returns the same `files` list with `idempotent=true`,
and may rebuild missing artifacts from the immutable snapshot. An unfinished task returns HTTP
409 with `detail.code=reports_not_available`. The completed task timeline contains
`report_generated` before the terminal `task_result` event; its `data` repeats the stable file
list and effective time.

`GET /api/files/{thread_id}/{name}` returns HTTP 404 with `detail.code=file_not_found` when the
task, report, or file is absent. Unsafe names return HTTP 400 with
`detail.code=invalid_file_path`; neither response includes a filesystem path.

`POST /api/task/{thread_id}/rerun` is the explicit Research Rerun command. Its required body is:

```json
{"user_id": "browser-7f3c1f7a", "idempotency_key": "rerun-2026-07-30-01"}
```

The command requires a completed snapshot with a resolved intent. It creates a new `thread_id`,
reuses the saved query, resolved intent, constraints, and applied preferences as inputs, and
returns:

```json
{
  "status": "started",
  "thread_id": "thread-new-child",
  "parent_snapshot_id": "thread-7b8cb4a9c23f",
  "lineage": {
    "relation": "rerun",
    "parent_snapshot_id": "thread-7b8cb4a9c23f",
    "parent_thread_id": "thread-7b8cb4a9c23f",
    "parent_run_id": "d41d8cd98f004204e9800998ecf8427e",
    "root_snapshot_id": "thread-7b8cb4a9c23f",
    "depth": 1,
    "command_idempotency_key": "rerun-2026-07-30-01",
    "changed_constraints": []
  },
  "idempotent": false
}
```

The child is a separate snapshot even when it fails, is cancelled, or returns a partial result;
the parent snapshot, events, result, reports, and provenance never change. Repeating the same
`idempotency_key` for the same parent returns the existing child with `idempotent=true`. Omitting
the key creates a new command each time. Idempotency is scoped to the parent snapshot, command
relation, and key; requests for one parent are serialized by the task command boundary, so
concurrent retries cannot create two children for the same keyed command.

`POST /api/task/{thread_id}/relaxation` accepts the same required `user_id` and is the only command that can apply a
`relaxation_suggestion`. It requires `confirmed: true` and selected `constraint_ids`; optional
`changes` can replace a constraint while retaining its stable ID. The response uses the same
`TaskRerunResponse` shape, with `lineage.relation=constraint_relaxation` and
`lineage.changed_constraints` recording each previous constraint, replacement/removal action, and
reason. An unconfirmed command returns `409 constraint_relaxation_confirmation_required` and never
creates a task. Constraint Relaxation is therefore a new task, never an in-place edit.

Clarification answers resume the same `thread_id` and event timeline, so clarification does not
create another Recent Research history item. Only Research Rerun and confirmed Constraint
Relaxation create new history items and lineage edges.

### Blocking clarification

The deterministic planner checks blocking ambiguity before Remembered Preference recall, marketplace
gateway calls, or price and landed-cost calculations. The supported fields and stable reason codes
are `mode/mode_ambiguous`, `product_variant/product_variant_ambiguous`, and
`destination/destination_ambiguous`. Missing optional color or style produces the visible
`Working Assumption` entries in the eventual result and does not enter this state.

`clarification_required.data` has this shape:

```json
{
  "field": "mode",
  "reason_code": "mode_ambiguous",
  "question": "你要比较不同产品，还是同一 Product Variant 的跨平台报价？",
  "data_mode": "sandbox"
}
```

Only `POST /api/task/{thread_id}/clarification` accepts a clarification response, and only while
the snapshot is `awaiting_clarification`:

```json
{"response": "比较不同产品"}
```

The successful response is `{"status":"resumed","thread_id":"...","field":"mode","idempotent":false}`.
The server persists `clarification_resolved` before starting the continuation. A repeated response
for the same recorded field is answered with the same shape and `idempotent=true` without creating
another task or event. Responses in any other task state return HTTP 409 with stable
`detail.code=clarification_not_awaiting`; invalid answers return HTTP 422 with
`detail.code=clarification_invalid_response` and leave the task awaiting.

Stable task error codes include `providers_unavailable`, `fx_rates_unavailable`,
`unsupported_capability`, `task_timeout`, `task_failed`, and `task_interrupted`.
`unsupported_capability` is emitted before marketplace search when the requested destination is
not China mainland; China mainland is currently the only supported landed-cost destination.
`fx_rates_unavailable` means every candidate uses a currency absent from `FX_RATES_JSON` and the
built-in reference table. If at least one candidate can be converted, candidates with missing
rates are excluded and `calculation_notice` discloses that partial exclusion.
When `FX_RATES_JSON` is configured, `FX_RATES_AS_OF` is required so the response can preserve the
effective date of the configured rates; the calculation basis is always exposed alongside it.

`POST /api/task/{thread_id}/cancel` is idempotent for known terminal tasks and accepts both
`running` and `awaiting_clarification` tasks. An awaiting task records `task_cancelled` without
starting marketplace work. Active tasks return:

```json
{"status": "cancelled", "thread_id": "thread-7b8cb4a9c23f"}
```

Submitting a new request with an active `thread_id` replaces that run. Cancellation of the
superseded worker is internal and does not emit `task_cancelled`; the replacement gets a new
`run_id`, starts a fresh timeline at sequence 1, and owns the thread's single terminal event. The
old WebSocket is closed with code `1012`, after which reconnect receives the replacement snapshot
instead of mixing both runs.

`DELETE /api/task/{thread_id}` permanently removes a research task. The command body identifies
the Anonymous Shopper ID that owns the task:

```json
{"user_id": "browser-7f3c1f7a"}
```

The service compares that ID with the task aggregate before changing anything and returns the
same not-found contract for another shopper. An active worker is first fenced by a durable
deletion tombstone, cancelled, and awaited; the tombstone and a per-task mutation lock prevent
late workers, external-call continuations, snapshot writes, report generation, and event
broadcasts from recreating the task. The task's durable snapshot, events, reports, task-owned
Reference Image copies, owner locks, WebSocket connection, and in-memory transport buffer are
then removed. The original upload records are not task-owned and remain available for their own
independent lifecycle. A deletion tombstone remains outside the task directory so a stale worker
cannot resurrect a deleted thread after process restart; reusing that thread ID returns HTTP 409
with `detail.code=task_deleted`.

The operation is idempotent for a valid thread ID, including after restart, so clients can also
clear stale local history entries:

```json
{"status": "deleted", "thread_id": "thread-7b8cb4a9c23f"}
```

When a task is accepted, each selected upload is bound at that point and copied into the task's
`reference-images/` boundary. The `session_created.data.reference_images` entries include
`upload_id`, safe generated `name`, `content_type`, `size`, `ownership="task_owned_copy"`, and
`bound_at`. Deleting the task removes only those copies. An upload that has never been selected
by a task remains an unbound temporary upload. User preferences, Task Override rules, and other
shopper tasks are not task-owned and are not deleted.

## Result representation

`task_result.data` and `snapshot.result` contain:

```json
{
  "thread_id": "thread-7b8cb4a9c23f",
  "final_answer": "...",
  "resolved_query": "预算 1200 元，找一款轻便降噪耳机，不要皮革",
  "resolved_intent": {
    "mode": "exact_offer_comparison",
    "budget_cny": 1200,
    "category": "降噪耳机",
    "material_preferences": [],
    "style_preferences": [],
    "hard_constraints": [],
    "soft_preferences": [],
    "destination": "中国大陆",
    "ranking_profile": {"priority_order": ["landed_cost", "preference_match", "evidence_quality", "delivery_time"], "explicit": false},
    "working_assumptions": [],
    "source": "computed"
  },
  "applied_preferences": {"material_preferences": [], "style_preferences": [], "soft_preferences": [], "avoid": []},
  "task_overrides": [],
  "constraint_relaxations": [],
  "product_evidence": [],
  "mode": "exact_offer_comparison",
  "recommendations": [],
  "comparison": [],
  "matching_offers": [],
  "alternative_candidates": [],
  "files": [
    {
      "file_id": "thread-7b8cb4a9c23f:markdown",
      "format": "markdown",
      "name": "shopping-report.md",
      "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.md",
      "content_type": "text/markdown; charset=utf-8"
    },
    {
      "file_id": "thread-7b8cb4a9c23f:json",
      "format": "json",
      "name": "shopping-report.json",
      "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.json",
      "content_type": "application/json; charset=utf-8"
    },
    {
      "file_id": "thread-7b8cb4a9c23f:pdf",
      "format": "pdf",
      "name": "shopping-report.pdf",
      "url": "/api/files/thread-7b8cb4a9c23f/shopping-report.pdf",
      "content_type": "application/pdf"
    }
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

### Recall provenance

`task_result.data.recall_provenance`, `snapshot.recall_provenance`, and
`ShoppingSummaryOutput.recall_provenance` are additive typed fields:

```json
{
  "mode": "hybrid",
  "channels": {
    "opensearch": {
      "channel": "opensearch",
      "configured": true,
      "state": "ready",
      "reason_code": "ready",
      "reason": "OpenSearch category knowledge was returned",
      "participated": true
    },
    "query_tower": {
      "channel": "query_tower",
      "configured": true,
      "state": "ready",
      "reason_code": "ready",
      "reason": "query embedding returned a finite vector",
      "participated": true
    },
    "item_tower": {
      "channel": "item_tower",
      "configured": true,
      "state": "ready",
      "reason_code": "ready",
      "reason": "item embeddings returned for every Product Evidence candidate",
      "participated": true
    },
    "faiss": {
      "channel": "faiss",
      "configured": true,
      "state": "ready",
      "reason_code": "ready",
      "reason": "Faiss returned candidate IDs and similarity scores",
      "participated": true
    }
  },
  "participating_channels": ["opensearch", "query_tower", "item_tower", "faiss"],
  "fallback_reason": null,
  "input_candidate_count": 12,
  "selected_candidate_count": 8,
  "personalization": {
    "configured": true,
    "state": "ready",
    "input_source": "remembered_preference",
    "preference_fields": ["style_preferences"],
    "preference_values": ["简约"],
    "signal": "user_tower",
    "dimension": 768,
    "matched_candidate_count": 8,
    "reason_code": "ready",
    "reason": "user tower encoded only explicit Remembered Preference",
    "participated": true
  }
}
```

`mode` is `hybrid` only when all four configured channels participated successfully,
`partial_hybrid` when at least one channel participated and another was degraded or unavailable,
and `deterministic_fallback` when no optional channel could safely contribute. Each channel is
reported independently with `configured`, `ready`, `degraded`, or `unavailable` state and a stable
`reason_code`; `participated` means that channel affected the current candidate selection or
ordering. The canonical channel order is OpenSearch, query tower, item tower, then Faiss.

`personalization` is a separate optional report rather than a fifth marketplace channel. Its
`state=ready` plus `participated=true` means that a typed User tower input changed the recall
signal. `input_source=remembered_preference` is the only input source that may activate it;
`preference_fields` and `preference_values` are the explicit saved fields sent to the adapter, and
the raw embedding is never persisted. `no_saved_preference`, `not_configured`,
`ann_backend_disabled`, `timeout`, `channel_failed`, `invalid_response`,
`dimension_mismatch`, and `item_tower_unavailable` are disclosed reason-code examples. Any such
degradation falls back to the existing recall path without changing Product Evidence.

OpenSearch contributes category knowledge and semantic context only. Query and item embeddings
and ANN scores can select or order candidates only from the marketplace `Product Evidence` already
returned by the parallel gateway branches. The query/item cosine is a deterministic secondary
recall signal after the ANN score, including tie-breaking when ANN scores are equal. Recall never
creates an offer, fills a missing product fact, or changes `Hard Constraint`, `Identity Evidence`,
`Landed Cost`, or the final deterministic ranking boundary. The user-tower signal is only a
deterministic recall/preference-match signal; personalized and non-personalized paths use the same
Product Evidence and decision engine. The raw `product_evidence` list remains
complete; only the selected subset is passed into cost calculation and ranking. When recall is
empty, unavailable, times out, or fails, the stable fallback preserves the original Product
Evidence order and discloses the reason.

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

`resolved_query`, `resolved_intent`, `applied_preferences`, `task_overrides`,
`constraint_relaxations`, and `product_evidence` are copied into the durable Research Snapshot as
well as the terminal result. `product_evidence` is the complete candidate evidence set, including
the source, provider provenance, stable identity fields, variant attributes, and retrieval time;
it is not a newly fetched view when an old snapshot is reopened. `report_references` at the snapshot
level points to the same generated files listed in `result.files`.

Each `product_evidence` item uses the normalized candidate fields (`item_id`, `platform`, `marketplace`,
`offer_id`, title, original price/currency, attributes, identity, variant attributes, availability,
`retrieved_at`, provenance, link kind, source, and nullable `identity_evidence`) from the same Pydantic
contract used by result rows. `identity_evidence` is nullable for raw Product Evidence until the
deterministic exact-mode matcher has evaluated it.

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

`GET /api/files/{thread_id}/{name}` serves only the stable report files listed in the completed
result. It returns the file's declared `content_type`, `Content-Disposition: attachment` with the
safe generated filename, and `X-Report-ID` with the stable `file_id`. Paths outside a task
directory, traversal names, and internal task state files are rejected. If a completed report file
is missing after a restart, the service deterministically rebuilds it from the Research Snapshot;
this never starts shopping research.

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

`user_id` is an Anonymous Shopper ID storage association key, not a login account, authenticated
identity, authentication credential, ownership proof, or authorization.
Public deployments must supply an authenticated identity at a trusted gateway and enforce ownership
for tasks, preferences, WebSockets, and files.

Only an explicit `remember`/`forget` Memory Update changes the Remembered Preference record. The
User tower receives a typed `UserTowerInput` containing that saved record and the Anonymous Shopper
ID. It never receives current query text, Task Override values, task outcome, or implicit behavior.
A `remember` command is scoped to future tasks, so its new value is not encoded for the command's
own task; a later task reads the persisted value. Deleting a task deletes only task state and cannot
delete or resurrect the preference record.

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
