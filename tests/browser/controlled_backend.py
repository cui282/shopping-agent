"""Deterministic HTTP/WebSocket backend for the release browser acceptance suite."""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

MARKETPLACES = ("amazon", "shopee", "aliexpress", "ebay")
SCENARIOS = {
    "task-ready",
    "running",
    "awaiting-clarification",
    "partial",
    "no-match",
    "empty",
    "error",
    "cancelled",
    "completed",
    "developer-diagnostic-mixed",
}

app = FastAPI(title="Shopping Agent controlled acceptance backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

tasks: dict[str, dict[str, Any]] = {}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def scenario_for(query: str) -> str:
    normalized = query.lower()
    for scenario in SCENARIOS:
        if scenario in normalized:
            return scenario
    return "completed"


def component(
    state: str,
    reason_code: str,
    reason: str,
    *,
    configured: bool = False,
    ready: bool = False,
) -> dict[str, Any]:
    return {
        "configured": configured,
        "ready": ready,
        "state": state,
        "reason_code": reason_code,
        "reason": reason,
    }


def readiness_body() -> dict[str, Any]:
    return {
        "status": "degraded",
        "task_ready": True,
        "environment": "test",
        "runtime_mode": "sandbox",
        "agent_mode": "rules",
        "requested_agent_mode": "rules",
        "preference_store": "memory",
        "providers": {
            name: {
                "configured": False,
                "state": "missing",
                "available": True,
                "source": "fixture",
                "failure_reason": None,
            }
            for name in MARKETPLACES
        },
        "capabilities": {
            "websocket_events": True,
            "persistent_snapshots": True,
            "image_upload": True,
            "image_analysis": False,
        },
        "required_actions": [],
        "data_mode": "sandbox",
        "developer_diagnostic_mode": True,
        "components": {
            "llm": component("unavailable", "not_configured", "controlled backend uses rules mode"),
            "marketplace_gateways": {
                name: component(
                    "disabled",
                    "sandbox_fixture_active",
                    "explicit Sandbox mode uses fixture providers; live data-provider channel is not used",
                )
                for name in MARKETPLACES
            },
            "redis": component(
                "disabled",
                "backend_disabled",
                "controlled backend uses process-local preference evaluation",
            ),
            "opensearch": component(
                "unavailable",
                "not_configured",
                "OpenSearch is not used by the controlled backend",
            ),
            "faiss": component(
                "disabled",
                "backend_disabled",
                "ANN backend is disabled in the controlled backend",
            ),
            "query_tower": component(
                "disabled",
                "ann_backend_disabled",
                "Query tower is not used by the controlled backend",
            ),
            "item_tower": component(
                "disabled",
                "ann_backend_disabled",
                "Item tower is not used by the controlled backend",
            ),
            "user_tower": component(
                "disabled",
                "ann_backend_disabled",
                "User tower is not used by the controlled backend",
            ),
            "storage": component(
                "ready",
                "writable",
                "controlled backend stores state in memory",
                configured=True,
                ready=True,
            ),
            "image_analysis": component(
                "disabled",
                "not_configured",
                "image analysis is disabled; image upload remains separate",
            ),
        },
        "preference_backend": {
            "requested_backend": "memory",
            "backend": "memory",
            "durability": "local_evaluation",
            "fallback_reason": None,
        },
        "recall": {
            "mode": "deterministic_fallback",
            "channels": {
                "opensearch": {
                    "channel": "opensearch",
                    "configured": False,
                    "state": "unavailable",
                    "reason_code": "not_configured",
                    "reason": "controlled backend does not use OpenSearch",
                    "participated": False,
                },
                "query_tower": {
                    "channel": "query_tower",
                    "configured": False,
                    "state": "unavailable",
                    "reason_code": "ann_backend_disabled",
                    "reason": "ANN backend is disabled; deterministic fallback remains active",
                    "participated": False,
                },
                "item_tower": {
                    "channel": "item_tower",
                    "configured": False,
                    "state": "unavailable",
                    "reason_code": "ann_backend_disabled",
                    "reason": "ANN backend is disabled; deterministic fallback remains active",
                    "participated": False,
                },
                "faiss": {
                    "channel": "faiss",
                    "configured": False,
                    "state": "unavailable",
                    "reason_code": "backend_disabled",
                    "reason": "ANN backend is disabled; deterministic fallback remains active",
                    "participated": False,
                },
            },
            "required_actions": [],
            "personalization": {
                "configured": False,
                "state": "unavailable",
                "input_source": "none",
                "preference_fields": [],
                "preference_values": [],
                "signal": "none",
                "dimension": None,
                "matched_candidate_count": 0,
                "reason_code": "not_configured",
                "reason": "User tower is not configured",
                "participated": False,
            },
        },
    }


def event(
    task: dict[str, Any],
    name: str,
    message: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    task["sequence"] += 1
    timestamp = now()
    return {
        "type": "monitor_event",
        "event_id": f"evt-{uuid.uuid4().hex}",
        "thread_id": task["thread_id"],
        "run_id": task["run_id"],
        "sequence": task["sequence"],
        "event": name,
        "message": message,
        "data": data,
        "timestamp": timestamp,
    }


def score_breakdown() -> dict[str, Any]:
    return {
        "priority_order": [
            "landed_cost",
            "preference_match",
            "evidence_quality",
            "delivery_time",
        ],
        "landed_cost_cny": 799,
        "landed_cost_score": 0.92,
        "preference_match_score": 0.8,
        "evidence_quality_score": 0.86,
        "delivery_time_days": 5,
        "delivery_time_score": 0.75,
    }


def shopping_plan(destination: str = "中国大陆") -> dict[str, Any]:
    return {
        "mode": "product_research",
        "budget_cny": 1200,
        "category": "降噪耳机",
        "material_preferences": [],
        "style_preferences": ["简约"],
        "hard_constraints": [
            {
                "id": "battery_hours:gte:20",
                "kind": "specification",
                "field": "battery_hours",
                "operator": "gte",
                "value": 20,
                "unit": "小时",
                "label": "续航至少 20 小时",
            }
        ],
        "soft_preferences": ["佩戴舒适"],
        "destination": destination,
        "ranking_profile": {
            "priority_order": [
                "landed_cost",
                "preference_match",
                "evidence_quality",
                "delivery_time",
            ],
            "explicit": False,
        },
        "working_assumptions": [],
        "source": "computed",
    }


def product(source: str = "fixture", platform: str = "amazon") -> dict[str, Any]:
    observed_at = now()
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
    )
    sandbox = source == "fixture"
    return {
        "item_id": f"controlled-{platform}",
        "platform": platform,
        "title": "Controlled acceptance headphones with a deliberately long title for wrapping",
        "price": 100,
        "currency": "USD",
        "price_cny": 700,
        "shipping_cny": 70,
        "insurance_cny": 0,
        "duty_cny": None,
        "import_vat_cny": None,
        "consumption_tax_cny": None,
        "import_tax_cny": 29,
        "landed_cny": 799,
        "eta_days": 5,
        "duty_tier": "标准",
        "rating": 4.7,
        "sales": 2384,
        "image_url": None,
        "product_url": "https://example.com/controlled-acceptance",
        "attributes": {},
        "source": source,
        "note": "受控验收数据，不代表实时商品",
        "marketplace": platform,
        "offer_id": None,
        "identity": {"gtin": None, "mpn": None, "brand": "Controlled", "model": "A-29"},
        "variant_attributes": {"material": "塑料", "battery_hours": 30},
        "availability": "in_stock",
        "retrieved_at": "2026-08-07T00:00:00Z",
        "provenance": {
            "kind": "sandbox_fixture" if sandbox else "marketplace_gateway",
            "provider": "controlled-acceptance-backend",
            "upstream_source": "local acceptance fixture",
        },
        "price_conversion": {
            "source_currency": "USD",
            "target_currency": "CNY",
            "rate_to_cny": 7,
            "purpose": "comparison_estimate",
            "rate_type": "sandbox_fixture" if sandbox else "provider_quote",
            "markup_status": "excluded",
            "markup_bps": None,
            "provider": "controlled-fx-feed",
            "source_reference": "controlled-price-fx-quote",
            "observed_at": observed_at,
            "expires_at": None if sandbox else expires_at,
        },
        "shipping_quote": {
            "quote_type": "sandbox_fixture" if sandbox else "carrier_quote",
            "currency": "USD",
            "total_amount": 10,
            "base_amount": 8,
            "surcharge_amount": 2,
            "discount_amount": 0,
            "actual_weight_kg": 0.42,
            "dimensional_weight_kg": 0.58,
            "chargeable_weight_kg": 0.58,
            "length_cm": 24,
            "width_cm": 18,
            "height_cm": 12,
            "dimensional_divisor": 6000,
            "origin_country": "US",
            "destination_country": "CN",
            "service_name": "Controlled Tracked Air",
            "eta_min_days": 4,
            "eta_max_days": 5,
            "provider": "controlled-logistics-feed",
            "source_reference": "controlled-shipping-quote",
            "observed_at": observed_at,
            "expires_at": None if sandbox else expires_at,
            "currency_conversion": {
                "source_currency": "USD",
                "target_currency": "CNY",
                "rate_to_cny": 7,
                "purpose": "comparison_estimate",
                "rate_type": "sandbox_fixture" if sandbox else "provider_quote",
                "markup_status": "excluded",
                "markup_bps": None,
                "provider": "controlled-fx-feed",
                "source_reference": "controlled-shipping-fx-quote",
                "observed_at": observed_at,
                "expires_at": None if sandbox else expires_at,
            },
        },
        "customs": {
            "hs_code": "8518300000",
            "country_of_origin": "CN",
            "destination_country": "CN",
            "ship_from_country": "US",
            "import_regime": "seller_collected",
            "rate_type": "provider_quote",
            "tariff_rate": None,
            "import_vat_rate": None,
            "consumption_tax_rate": 0,
            "personal_postal_tax_rate": None,
            "personal_postal_assessed_value_cny": None,
            "personal_postal_total_value_cny": None,
            "personal_postal_value_limit_cny": None,
            "personal_postal_tax_exemption_threshold_cny": None,
            "personal_postal_single_indivisible_item": None,
            "personal_postal_eligible": None,
            "seller_collected_tax_cny": 29,
            "insurance_cny": 0,
            "valuation": None,
            "cross_border_ecommerce_eligible": None,
            "provider": "controlled-customs-feed",
            "source_reference": "controlled-checkout-tax-quote",
            "effective_date": "2026-08-11",
        },
        "link_kind": "marketplace_search",
        "identity_evidence": {
            "decision": "not_required",
            "basis": "not_required",
            "matched_fields": [],
            "missing_fields": [],
            "conflicting_fields": [],
            "explanation": "不同商品推荐不要求跨平台同款证明。",
        },
        "shipping_estimate": {
            "estimated": True,
            "source": "controlled-logistics-feed",
            "calculation_basis": (
                "Controlled Tracked Air；USD 10.00；计费重量 0.58kg；时效 4-5 天"
            ),
        },
        "duty_estimate": {
            "estimated": True,
            "source": "controlled acceptance",
            "calculation_basis": "fixture",
        },
        "delivery_estimate": {
            "estimated": True,
            "source": "controlled-logistics-feed",
            "calculation_basis": "Controlled Tracked Air 报价时效 4-5 天；排序采用上限",
        },
        "tax_estimate": {
            "estimated": True,
            "source": "controlled-customs-feed",
            "calculation_basis": "使用受控结算页已代收进口税费报价",
        },
        "tax_breakdown": {
            "import_regime": "seller_collected",
            "calculation_method": "provider_quote",
            "hs_code": "8518300000",
            "country_of_origin": "CN",
            "destination_country": "CN",
            "customs_value_cny": 770,
            "customs_valuation": None,
            "rate_type": "provider_quote",
            "tariff_rate": None,
            "import_vat_rate": None,
            "consumption_tax_rate": 0,
            "personal_postal_tax_rate": None,
            "personal_postal_assessed_value_cny": None,
            "personal_postal_total_value_cny": None,
            "personal_postal_value_limit_cny": None,
            "personal_postal_tax_exemption_threshold_cny": None,
            "personal_postal_single_indivisible_item": None,
            "policy_factor": 1,
            "tariff_cny": None,
            "import_vat_cny": None,
            "consumption_tax_cny": None,
            "tax_before_exemption_cny": None,
            "tax_exemption_cny": 0,
            "tax_exemption_reason": None,
            "total_import_tax_cny": 29,
            "provider": "controlled-customs-feed",
            "source_reference": "controlled-checkout-tax-quote",
            "effective_date": "2026-08-11",
            "calculation_basis": "使用受控结算页已代收进口税费报价",
        },
        "reason": "受控后端返回的可追溯验收候选。",
        "rank": 1,
        "constraint_evaluations": [],
        "score_breakdown": score_breakdown(),
        "offer_kind": "research_candidate",
    }


def result_for(task: dict[str, Any], scenario: str) -> dict[str, Any]:
    mixed = scenario == "developer-diagnostic-mixed"
    source = "live" if mixed else "fixture"
    item = product(source=source)
    providers: dict[str, Any] = {
        "amazon": {
            "source": source,
            "provider": "controlled-acceptance-backend",
            "status": "ok",
            "fallback_reason": None,
            "failure_reason": None,
        },
    }
    recommendations = [item]
    evidence = [item]
    comparison = [item]
    unavailable: list[str] = []
    result_kind = "sandbox"
    final_answer = "受控后端已完成一次购物研究，结果仅用于浏览器验收。"

    if scenario == "partial":
        providers["ebay"] = {
            "source": "fixture",
            "provider": "controlled-acceptance-backend",
            "status": "unavailable",
            "fallback_reason": None,
            "failure_reason": "request_failed",
        }
        unavailable = ["ebay"]
        result_kind = "partial"
        final_answer = "部分平台返回了商品证据；另一个平台以稳定失败原因标记为不可用。"
    elif scenario == "empty":
        providers = {
            name: {
                "source": "fixture",
                "provider": "controlled-acceptance-backend",
                "status": "unavailable",
                "fallback_reason": None,
                "failure_reason": "empty_response",
            }
            for name in MARKETPLACES
        }
        recommendations = []
        evidence = []
        comparison = []
        unavailable = list(MARKETPLACES)
        result_kind = "partial"
        final_answer = "受控后端模拟了平台没有返回商品证据的空结果。"
    elif scenario == "no-match":
        final_answer = "受控后端返回了候选，但它们没有同时满足全部硬性条件。"
        item["constraint_evaluations"] = [
            {
                "constraint": {
                    "id": "material:avoid",
                    "kind": "material",
                    "field": "material",
                    "operator": "not_contains",
                    "value": "塑料",
                    "unit": None,
                    "label": "不要塑料",
                },
                "status": "violated",
                "reason_code": "material_forbidden",
                "explanation": "候选材质包含被排除的材料。",
                "evidence": [],
            }
        ]
        recommendations = []
    elif mixed:
        providers["ebay"] = {
            "source": "fixture",
            "provider": "controlled-acceptance-backend",
            "status": "degraded",
            "fallback_reason": "developer diagnostic fixture fallback",
            "failure_reason": "request_failed",
        }
        result_kind = "partial"
        final_answer = "开发诊断模式下混合了受控实时标记和演示标记；这不是普通用户结果。"

    return {
        "thread_id": task["thread_id"],
        "final_answer": final_answer,
        "resolved_query": task["query"],
        "resolved_intent": task["resolved_intent"],
        "applied_preferences": {
            "material_preferences": [],
            "style_preferences": [],
            "soft_preferences": [],
            "avoid": [],
        },
        "task_overrides": [],
        "constraint_relaxations": [],
        "product_evidence": evidence,
        "mode": "product_research",
        "recommendations": recommendations,
        "comparison": comparison,
        "matching_offers": comparison,
        "alternative_candidates": [],
        "files": [],
        "provider_mode": "mixed" if mixed else "sandbox",
        "providers": providers,
        "calculation_notice": (
            "比较货币为 CNY；商品与运费使用逐报价换算证据；"
            "最终支付、运输与税费以平台、发卡行、承运商及海关核定为准。"
        ),
        "exchange_rate": {
            "base_currency": "CNY",
            "source": "offer-level-quotes",
            "effective_date": now(),
            "calculation_basis": "original_amount * rate_to_cny",
            "providers": ["controlled-fx-feed"],
            "quote_count": 1,
            "settlement_notice": "人民币金额仅用于受控比较，最终支付以结算页和发卡行为准。",
        },
        "calculation_exclusions": [],
        "shipping_exclusions": [],
        "tax_exclusions": [],
        "ranking_profile": {
            "priority_order": [
                "landed_cost",
                "preference_match",
                "evidence_quality",
                "delivery_time",
            ],
            "explicit": False,
        },
        "data_mode": "mixed" if mixed else "sandbox",
        "result_kind": result_kind,
        "unavailable_marketplaces": unavailable,
        "unverified_candidates": [],
        "exclusions": [],
        "working_assumptions": [],
        "relaxation_suggestions": [],
        "match_status": "no_match" if scenario == "no-match" else "matched",
        "preference_decisions": [],
        "recall_provenance": None,
    }


def snapshot(task: dict[str, Any]) -> dict[str, Any]:
    clarification = task.get("clarification")
    return {
        "snapshot_id": task["thread_id"],
        "thread_id": task["thread_id"],
        "run_id": task["run_id"],
        "generation": 0,
        "status": task["status"],
        "query": task["query"],
        "user_id": task["user_id"],
        "data_mode": task.get("data_mode", "sandbox"),
        "created_at": task["created_at"],
        "updated_at": task.get("updated_at", task["created_at"]),
        "lineage": None,
        "resolved_query": task.get("result", {}).get("resolved_query")
        if task.get("result")
        else None,
        "resolved_intent": task.get("resolved_intent"),
        "mode": "product_research" if task.get("result") else None,
        "working_assumptions": [],
        "applied_preferences": {
            "material_preferences": [],
            "style_preferences": [],
            "soft_preferences": [],
            "avoid": [],
        },
        "task_overrides": [],
        "constraint_relaxations": [],
        "provider_coverage": task.get("result", {}).get("providers", {})
        if task.get("result")
        else {},
        "product_evidence": task.get("result", {}).get("product_evidence", [])
        if task.get("result")
        else [],
        "exchange_rate": task.get("result", {}).get("exchange_rate")
        if task.get("result")
        else None,
        "report_references": [],
        "events": task["events"],
        "result": task.get("result"),
        "clarification": clarification,
        "clarification_answers": task.get("clarification_answers", {}),
        "error_code": task.get("error_code"),
        "error": task.get("error"),
        "recall_provenance": None,
    }


def append(task: dict[str, Any], name: str, message: str, data: dict[str, Any]) -> None:
    task["events"].append(event(task, name, message, data))
    task["updated_at"] = now()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "service": "shopping-agent", "version": "acceptance"}


@app.get("/api/readiness")
async def readiness() -> dict[str, Any]:
    return readiness_body()


@app.get("/api/research")
async def research(user_id: str) -> dict[str, Any]:
    return {"snapshots": [snapshot(task) for task in tasks.values() if task["user_id"] == user_id]}


@app.get("/api/preferences/{user_id}")
async def preferences(user_id: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "preferences": {},
        "backend": readiness_body()["preference_backend"],
    }


@app.put("/api/preferences/{user_id}")
async def update_preferences(user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    del payload
    return await preferences(user_id)


@app.delete("/api/preferences/{user_id}")
async def delete_preferences(user_id: str) -> dict[str, Any]:
    return {"status": "deleted", **(await preferences(user_id))}


@app.post("/api/task")
async def create_task(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=422, detail="query is required")
    user_id = str(payload.get("user_id", "acceptance-user"))
    scenario = scenario_for(query)
    thread_id = f"thread-{uuid.uuid4().hex[:12]}"
    task = {
        "thread_id": thread_id,
        "run_id": uuid.uuid4().hex,
        "query": query,
        "user_id": user_id,
        "scenario": scenario,
        "status": "running",
        "data_mode": "mixed" if scenario == "developer-diagnostic-mixed" else "sandbox",
        "created_at": now(),
        "updated_at": now(),
        "sequence": 0,
        "events": [],
        "clarification": None,
        "clarification_answers": {},
        "result": None,
        "resolved_intent": None,
        "error_code": None,
        "error": None,
    }
    tasks[thread_id] = task
    append(
        task,
        "session_created",
        "研究任务已创建",
        {"thread_id": thread_id, "reference_images": [], "data_mode": task["data_mode"]},
    )

    if scenario != "awaiting-clarification":
        task["resolved_intent"] = shopping_plan()
        append(
            task,
            "intent_resolved",
            "已理解本次购物需求",
            {
                "resolved_query": query,
                "resolved_intent": task["resolved_intent"],
                "applied_preferences": {
                    "material_preferences": [],
                    "style_preferences": [],
                    "soft_preferences": [],
                    "avoid": [],
                },
                "task_overrides": [],
                "constraint_relaxations": [],
                "data_mode": task["data_mode"],
            },
        )

    if scenario == "awaiting-clarification":
        task["status"] = "awaiting_clarification"
        task["clarification"] = {
            "field": "destination",
            "reason_code": "destination_ambiguous",
            "question": "请确认这次商品配送到哪里？",
        }
        append(
            task,
            "clarification_required",
            task["clarification"]["question"],
            {**task["clarification"], "data_mode": task["data_mode"]},
        )
    elif scenario in {
        "task-ready",
        "completed",
        "partial",
        "no-match",
        "empty",
        "developer-diagnostic-mixed",
    }:
        task["result"] = result_for(task, scenario)
        task["status"] = "completed"
        append(task, "task_result", "研究任务已完成", task["result"])
    elif scenario == "error":
        task["status"] = "error"
        task["error_code"] = "task_failed"
        task["error"] = "受控后端模拟了研究失败，请检查错误状态。"
        append(
            task,
            "error",
            task["error"],
            {"thread_id": thread_id, "code": task["error_code"], "data_mode": task["data_mode"]},
        )
    return {"status": "started", "thread_id": thread_id}


@app.get("/api/task/{thread_id}")
async def get_task(thread_id: str) -> dict[str, Any]:
    task = tasks.get(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return snapshot(task)


@app.post("/api/task/{thread_id}/cancel")
async def cancel_task(thread_id: str) -> dict[str, Any]:
    task = tasks.get(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] in {"running", "awaiting_clarification"}:
        task["status"] = "cancelled"
        task["clarification"] = None
        append(
            task,
            "task_cancelled",
            "研究任务已取消",
            {"thread_id": thread_id, "data_mode": task["data_mode"]},
        )
    return {"status": task["status"], "thread_id": thread_id}


@app.post("/api/task/{thread_id}/clarification")
async def clarify_task(thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    task = tasks.get(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] != "awaiting_clarification":
        raise HTTPException(status_code=409, detail="task is not awaiting clarification")
    response = str(payload.get("response", "")).strip()
    if not response:
        raise HTTPException(status_code=422, detail="response is required")
    prompt = task["clarification"]
    task["clarification_answers"][prompt["field"]] = response
    task["clarification"] = None
    task["status"] = "running"
    append(
        task,
        "clarification_resolved",
        "澄清已提交，继续研究",
        {
            "field": prompt["field"],
            "reason_code": prompt["reason_code"],
            "response": response,
            "resolved_value": response,
            "data_mode": task["data_mode"],
        },
    )
    task["resolved_intent"] = shopping_plan(response)
    append(
        task,
        "intent_resolved",
        "已理解本次购物需求",
        {
            "resolved_query": task["query"],
            "resolved_intent": task["resolved_intent"],
            "applied_preferences": {
                "material_preferences": [],
                "style_preferences": [],
                "soft_preferences": [],
                "avoid": [],
            },
            "task_overrides": [],
            "constraint_relaxations": [],
            "data_mode": task["data_mode"],
        },
    )
    task["result"] = result_for(task, "completed")
    task["status"] = "completed"
    append(task, "task_result", "研究任务已完成", task["result"])
    return {
        "status": "resumed",
        "thread_id": thread_id,
        "field": prompt["field"],
        "idempotent": False,
    }


@app.delete("/api/task/{thread_id}")
async def delete_task(thread_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    del payload
    if thread_id not in tasks:
        raise HTTPException(status_code=404, detail="task not found")
    tasks.pop(thread_id, None)
    return {"status": "deleted", "thread_id": thread_id}


@app.post("/api/upload")
async def upload() -> dict[str, Any]:
    return {
        "upload_id": uuid.uuid4().hex,
        "name": "controlled.png",
        "content_type": "image/png",
        "size": 1,
    }


@app.websocket("/ws/{thread_id}")
async def websocket(websocket: WebSocket, thread_id: str) -> None:
    task = tasks.get(thread_id)
    if task is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    try:
        await websocket.send_json(
            {"type": "task_snapshot", "snapshot": snapshot(task), "timestamp": now()}
        )
        while task["status"] in {"running", "awaiting_clarification"}:
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=1)
            except TimeoutError:
                continue
            if message == "ping":
                await websocket.send_text("pong")
        await websocket.send_json(
            {"type": "task_snapshot", "snapshot": snapshot(task), "timestamp": now()}
        )
    except (WebSocketDisconnect, RuntimeError):
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
