from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from app.agent.dispatch_tool import dispatch_tool
from app.agent.llm import active_agent_mode, allow_rules_fallback, get_llm, requested_mode
from app.agent.system_prompt import build_system_prompt
from app.api.monitor import Monitor
from app.config import get_settings
from app.memory.injector import extract_preferences, merge_preference_records, merge_preferences
from app.memory.store import PreferenceStore
from app.schemas import (
    DataMode,
    ItemSearchOutput,
    Platform,
    ProviderFailureReason,
    ProviderMetadata,
    ShoppingSummaryOutput,
    TaskRequest,
    ToolEndEventData,
)
from app.tools import (
    category_insight,
    item_picker,
    item_search,
    planner,
    price_compare,
    shipping_calc,
    shopping_summary,
)
from app.utils.thread_ctx import get_thread_id

T = TypeVar("T")


class ProvidersUnavailableError(RuntimeError):
    """All enabled marketplace providers failed to return usable data."""


ADVISORY_TOOLS = [planner]


async def _call_tool(
    monitor: Monitor,
    name: str,
    args: dict[str, Any],
    call: Callable[[], Awaitable[T]],
    *,
    event_thread_id: str | None = None,
    data_mode: DataMode | None = None,
    failure_metadata: ProviderMetadata | None = None,
) -> T:
    thread_id = event_thread_id or get_thread_id()
    event_data_mode = data_mode or get_settings().data_mode
    await monitor.emit(
        thread_id,
        "tool_start",
        message=f"正在调用 {name} 工具",
        data={"tool_name": name, "args": args, "data_mode": event_data_mode},
    )
    started = time.perf_counter()
    try:
        result = await call()
    except Exception as exc:
        metadata = failure_metadata
        if metadata is None:
            metadata = ProviderMetadata(
                source="fixture" if event_data_mode == "sandbox" else "computed",
                provider=name,
                status="unavailable",
                fallback_reason=type(exc).__name__,
            )
        failed = ToolEndEventData(
            tool_name=name,
            duration_ms=round((time.perf_counter() - started) * 1000),
            outcome="failure",
            source=metadata.source,
            provider=metadata.provider,
            status="unavailable",
            fallback_reason=metadata.fallback_reason,
            failure_reason=metadata.failure_reason,
            data_mode=event_data_mode,
        )
        await monitor.emit(
            thread_id,
            "tool_end",
            message=f"{name} 工具调用失败",
            data=failed.model_dump(mode="json"),
        )
        raise

    provider = getattr(result, "provider", None)
    if provider is not None:
        metadata = provider.model_dump(mode="json")
        provider_status = metadata["status"]
        outcome = (
            "degraded"
            if provider_status == "degraded"
            else "failure"
            if provider_status == "unavailable"
            else "success"
        )
        source = metadata["source"]
        provider_name = metadata["provider"]
        fallback_reason = metadata["fallback_reason"]
        failure_reason = metadata["failure_reason"]
    else:
        result_source = getattr(result, "source", "computed")
        source = (
            result_source
            if result_source in {"live", "curated", "fixture", "computed"}
            else "computed"
        )
        provider_name = name
        provider_status = "ok"
        outcome = "success"
        fallback_reason = None
        failure_reason = None
    completed = ToolEndEventData(
        tool_name=name,
        duration_ms=round((time.perf_counter() - started) * 1000),
        outcome=outcome,
        source=source,
        provider=provider_name,
        status=provider_status,
        fallback_reason=fallback_reason,
        failure_reason=failure_reason,
        data_mode=event_data_mode,
    )
    await monitor.emit(
        thread_id,
        "tool_end",
        message=f"{name} 工具调用完成",
        data=completed.model_dump(mode="json"),
    )
    return result


async def _run_react_advisory(query: str, preferences: dict[str, Any]) -> str:
    """Invoke the configured LangGraph ReAct agent as the intent-analysis entry point."""

    from langgraph.prebuilt import create_react_agent

    kwargs: dict[str, Any] = {"model": get_llm(), "tools": ADVISORY_TOOLS}
    signature = inspect.signature(create_react_agent)
    prompt = (
        build_system_prompt(preferences)
        + "\n本轮只完成意图分析；如需工具，仅调用 planner，然后给出精炼计划。"
    )
    if "prompt" in signature.parameters:
        kwargs["prompt"] = prompt
    else:
        kwargs["state_modifier"] = prompt
    graph = create_react_agent(**kwargs)
    response = await graph.ainvoke(
        {"messages": [("user", query)]},
        config={"recursion_limit": 8},
    )
    messages = response.get("messages", [])
    if not messages:
        return ""
    content = getattr(messages[-1], "content", "")
    return str(content)[:500]


async def run_agent(
    request: TaskRequest,
    monitor: Monitor,
    store: PreferenceStore,
    reference_images: list[dict[str, Any]] | None = None,
    data_mode: DataMode | None = None,
) -> ShoppingSummaryOutput:
    thread_id = get_thread_id()
    settings = get_settings()
    task_data_mode = data_mode or settings.data_mode
    remembered = await store.get(request.user_id)

    await monitor.emit(
        thread_id,
        "assistant_call",
        message="Think：正在理解预算、品类和约束",
        data={
            "step": "thinking",
            "preview": request.query[:160],
            "agent_mode": active_agent_mode(),
            "reference_images": reference_images or [],
            "data_mode": task_data_mode,
        },
    )
    agent_mode = active_agent_mode()
    if requested_mode() == "llm" and agent_mode == "unavailable":
        raise RuntimeError("AGENT_MODE=llm but model credentials are not configured")
    if agent_mode == "llm":
        try:
            preview = await _run_react_advisory(request.query, remembered)
            await monitor.emit(
                thread_id,
                "assistant_call",
                message="LangGraph ReAct 已完成意图分析",
                data={
                    "step": "thinking",
                    "preview": preview,
                    "source": "live",
                    "data_mode": task_data_mode,
                },
            )
        except Exception as exc:
            if not allow_rules_fallback():
                raise
            await monitor.emit(
                thread_id,
                "assistant_call",
                message="模型不可用，继续使用规则编排",
                data={
                    "step": "thinking",
                    "source": "computed",
                    "fallback_reason": type(exc).__name__,
                    "data_mode": task_data_mode,
                },
            )

    plan = await _call_tool(
        monitor, "planner", {"query": request.query}, lambda: planner(request.query)
    )
    plan = merge_preferences(plan, remembered)
    insight = await _call_tool(
        monitor,
        "category_insight",
        {"category": plan.category, "depth": "quick"},
        lambda: category_insight(plan.category),
    )

    await monitor.emit(
        thread_id,
        "assistant_call",
        message=f"Act：并行检索 {len(settings.enabled_marketplaces)} 个已启用平台",
        data={
            "step": "acting",
            "category": insight.category,
            "components": insight.components,
            "platforms": list(settings.enabled_marketplaces),
            "data_mode": task_data_mode,
        },
    )
    platforms = [cast(Platform, name) for name in settings.enabled_marketplaces]

    async def search_branch(demand: dict[str, Any]) -> ItemSearchOutput:
        platform: Platform = demand["platform"]
        failure_metadata = ProviderMetadata(
            source="fixture" if task_data_mode == "sandbox" else "live",
            provider=platform,
            status="unavailable",
            fallback_reason="provider request failed: unexpected exception",
            failure_reason="request_failed",
        )
        try:
            return await _call_tool(
                monitor,
                "item_search",
                {"platform": platform, "top_k": 20},
                lambda: item_search(request.query, platform, top_k=20, user_id=request.user_id),
                event_thread_id=thread_id,
                data_mode=task_data_mode,
                failure_metadata=failure_metadata,
            )
        except Exception as exc:  # noqa: BLE001 - one marketplace cannot cancel siblings
            failure_reason: ProviderFailureReason = "request_failed"
            failure_metadata = failure_metadata.model_copy(
                update={
                    "fallback_reason": f"provider request failed: {type(exc).__name__}",
                    "failure_reason": failure_reason,
                }
            )
            return ItemSearchOutput(
                platform=platform,
                candidates=[],
                total_recall=0,
                truncated=False,
                provider=failure_metadata,
            )

    searches = await dispatch_tool(
        [{"platform": platform, "query": request.query} for platform in platforms],
        search_branch,
        monitor,
        data_mode=task_data_mode,
    )
    candidates = [candidate for result in searches for candidate in result.candidates]
    providers = {result.platform: result.provider for result in searches}

    await monitor.emit(
        thread_id,
        "assistant_call",
        message="Observe：正在统一价格并估算到手成本",
        data={
            "step": "observing",
            "candidate_count": len(candidates),
            "providers": {
                name: metadata.model_dump(mode="json") for name, metadata in providers.items()
            },
            "data_mode": task_data_mode,
        },
    )
    if not candidates or (
        providers and all(metadata.status == "unavailable" for metadata in providers.values())
    ):
        raise ProvidersUnavailableError("all enabled marketplace providers are unavailable")

    prices = await _call_tool(
        monitor,
        "price_compare",
        {"candidate_count": len(candidates), "base_currency": "CNY"},
        lambda: price_compare(candidates),
    )
    shipping = await _call_tool(
        monitor,
        "shipping_calc",
        {"item_count": len(prices.ranked), "destination": plan.destination},
        lambda: shipping_calc(prices.ranked, destination=plan.destination),
    )
    picks = await _call_tool(
        monitor,
        "item_picker",
        {"item_count": len(shipping.items), "max_items": 3},
        lambda: item_picker(shipping, plan),
    )
    await monitor.emit(
        thread_id,
        "assistant_call",
        message="Reflect：核对硬约束与推荐理由",
        data={
            "step": "reflecting",
            "selected_count": len(picks.recommendations),
            "data_mode": task_data_mode,
        },
    )
    result = await _call_tool(
        monitor,
        "shopping_summary",
        {"recommendation_count": len(picks.recommendations)},
        lambda: shopping_summary(
            picks,
            shipping.items,
            providers=providers,
            rate_source=prices.rate_source,
            rates_as_of=prices.rates_as_of,
            excluded_currencies=prices.excluded_currencies,
            shipping_basis=shipping.calculation_basis,
            unavailable_marketplaces=[
                result.platform for result in searches if result.provider.status == "unavailable"
            ],
            data_mode=task_data_mode,
        ),
    )
    extracted = extract_preferences(request.query, plan)
    if extracted:
        await store.put(request.user_id, merge_preference_records(remembered, extracted))
    return result
