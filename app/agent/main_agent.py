from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar, cast

from app.agent.budget import budget_state, choose_route, record_usage, start_budget
from app.agent.dispatch_tool import dispatch_tool
from app.agent.guard import record_tool_call
from app.agent.llm import active_agent_mode, allow_rules_fallback, get_llm, requested_mode
from app.agent.system_prompt import build_system_prompt
from app.agent.tool_registry import get_execution_plan, reset_execution_plan, task_tool
from app.api.monitor import Monitor
from app.compress import (
    ContextCompressionSettings,
    ContextMessage,
    ModelContext,
    build_context_messages,
    build_context_summary_from_events,
    build_model_context,
    safe_bounded_context,
)
from app.config import MARKETPLACES, get_settings
from app.harness.defaults import install_default_hooks
from app.harness.middleware import harness
from app.harness.phase import phase_machine
from app.memory.commands import (
    execute_memory_commands,
    parse_memory_commands,
    remembered_for_task,
    strip_memory_commands,
)
from app.memory.injector import resolve_preferences
from app.memory.store import PreferenceStore
from app.observability import get_observer
from app.recall.orchestrator import RecallOrchestrator
from app.schemas import (
    CategoryInsightOutput,
    ConstraintRelaxation,
    ConstraintRelaxationChange,
    ContextPreferenceSource,
    DataMode,
    HardConstraint,
    ItemSearchOutput,
    MonitorEvent,
    Platform,
    ProviderFailureReason,
    ProviderMetadata,
    RecallProvenance,
    RecallResult,
    RememberedPreference,
    ShoppingPlan,
    ShoppingSummaryOutput,
    TaskOverride,
    TaskRequest,
    ToolEndEventData,
    UserTowerInput,
)
from app.security import ToolCallDenied, pre_tool_check
from app.tools import (
    category_insight,
    item_picker,
    item_search,
    planner,
    price_compare,
    shipping_calc,
    shopping_summary,
)
from app.tools.category_insight import curated_category_insight
from app.tools.clarification import (
    BlockingAmbiguity,
    apply_clarification_context,
    detect_blocking_ambiguity,
)
from app.tools.destination import SUPPORTED_DESTINATION, is_supported_destination
from app.utils.thread_ctx import get_thread_id

T = TypeVar("T")


class ProvidersUnavailableError(RuntimeError):
    """All enabled marketplace providers failed to return usable data."""


class UnsupportedCapabilityError(RuntimeError):
    """The requested destination is outside the current landed-cost capability."""


class BlockingAmbiguityError(RuntimeError):
    """The request needs one deterministic clarification before research can continue."""

    def __init__(self, ambiguity: BlockingAmbiguity) -> None:
        self.ambiguity = ambiguity
        super().__init__(ambiguity.question)


ADVISORY_TOOLS = [planner, task_tool]
recall_orchestrator = RecallOrchestrator()
install_default_hooks()


async def _category_insight_with_fallback(category: str) -> CategoryInsightOutput:
    try:
        return await asyncio.wait_for(
            category_insight(category), timeout=get_settings().recall_timeout_seconds
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - category knowledge is optional
        return curated_category_insight(
            category,
            fallback_reason=f"category insight failed: {type(exc).__name__}",
        )


def _apply_constraint_relaxations(
    plan: ShoppingPlan,
    changes: list[ConstraintRelaxationChange],
) -> tuple[ShoppingPlan, list[ConstraintRelaxation]]:
    if not changes:
        return plan, []

    by_id = {constraint.id: constraint for constraint in plan.hard_constraints}
    applied: list[ConstraintRelaxation] = []
    replacements = {change.constraint_id: change for change in changes}
    unknown = sorted(set(replacements) - set(by_id))
    if unknown:
        raise ValueError(f"unknown hard constraint: {', '.join(unknown)}")

    next_constraints: list[HardConstraint] = []
    for constraint in plan.hard_constraints:
        change = replacements.get(constraint.id)
        if change is None:
            next_constraints.append(constraint)
            continue
        replacement = change.replacement
        if replacement is not None and replacement.id != constraint.id:
            raise ValueError("a relaxed constraint replacement must keep the original id")
        applied.append(
            ConstraintRelaxation(
                constraint_id=constraint.id,
                previous=constraint,
                replacement=replacement,
                action="replaced" if replacement is not None else "removed",
                reason=change.reason,
            )
        )
        if replacement is not None:
            next_constraints.append(replacement)

    return plan.model_copy(update={"hard_constraints": next_constraints}), applied


def _task_overrides(decisions) -> list[TaskOverride]:
    overridden_by_field: dict[str, list[str]] = {}
    for decision in decisions:
        if decision.status == "overridden" and decision.source == "remembered_preference":
            overridden_by_field.setdefault(decision.field, []).append(decision.value)
    return [
        TaskOverride(
            field=decision.field,
            value=decision.value,
            overridden_values=overridden_by_field.get(decision.field, []),
            reason=decision.reason,
        )
        for decision in decisions
        if decision.status == "applied"
        and decision.source == "current_request"
        and overridden_by_field.get(decision.field)
    ]


async def _call_tool(
    monitor: Monitor,
    name: str,
    args: dict[str, Any],
    call: Callable[[], Awaitable[T]],
    *,
    event_thread_id: str | None = None,
    observer_thread_id: str | None = None,
    data_mode: DataMode | None = None,
    failure_metadata: ProviderMetadata | None = None,
) -> T:
    thread_id = event_thread_id or get_thread_id()
    event_data_mode = data_mode or get_settings().data_mode
    hook_context = await harness.run(
        "pre_tool_call",
        {
            "thread_id": thread_id,
            "tool_name": name,
            "tool_args": args,
            "tool_call_id": f"{thread_id}:{name}",
            "tool_history": phase_machine.tool_history(),
            "query": args.get("query") or args.get("category"),
        },
    )
    args = dict(hook_context.get("tool_args", args))
    denied = pre_tool_check({"name": name})
    if denied is not None:
        raise ToolCallDenied(denied["error"])
    record_tool_call(name, args)
    await monitor.emit(
        thread_id,
        "tool_start",
        message=f"正在调用 {name} 工具",
        data={"tool_name": name, "args": args, "data_mode": event_data_mode},
    )
    started = time.perf_counter()
    try:
        result = await call()
        hook_context = await harness.run(
            "post_tool_call",
            {
                **hook_context,
                "tool_result": result,
            },
        )
        result = cast(T, hook_context.get("tool_result", result))
        phase_machine.observe_tool(name)
        await harness.run(
            "post_reflect",
            {
                **hook_context,
                "tool_result": result,
                "tool_history": phase_machine.tool_history(),
            },
        )
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
        get_observer().tool_span(
            observer_thread_id or thread_id,
            name=name,
            duration_ms=failed.duration_ms,
            status=failed.status,
            route=budget_state().route,
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
    recall_provenance = getattr(result, "provenance", None)
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
        recall_provenance=(
            recall_provenance if isinstance(recall_provenance, RecallProvenance) else None
        ),
    )
    await monitor.emit(
        thread_id,
        "tool_end",
        message=f"{name} 工具调用完成",
        data=completed.model_dump(mode="json"),
    )
    get_observer().tool_span(
        observer_thread_id or thread_id,
        name=name,
        duration_ms=completed.duration_ms,
        status=completed.status,
        route=budget_state().route,
    )
    return result


async def _run_react_advisory(
    query: str,
    preferences: dict[str, Any],
    model_context: ModelContext | None = None,
    user_id: str | None = None,
) -> str:
    """Invoke the bounded model controller for intent and execution planning.

    The controller may call ``planner`` and ``task_tool``. Result-producing tools remain owned by
    the deterministic application path below, which keeps Product Evidence and ranking outside
    the language model.
    """

    from langgraph.prebuilt import create_react_agent

    reset_execution_plan()
    route = choose_route(model_context.estimated_tokens if model_context is not None else 0)
    if route == "fallback":
        return ""
    kwargs: dict[str, Any] = {"model": get_llm(route), "tools": ADVISORY_TOOLS}
    signature = inspect.signature(create_react_agent)
    prompt = (
        build_system_prompt(preferences, user_id=user_id)
        + "\n本轮负责理解意图并通过 task_tool 提出执行计划。只能使用 planner 和 task_tool；"
        "不得编造商品事实、价格、库存或排序结论。task_tool 只选择平台、并行策略和标准步骤，"
        "最终 Product Evidence 与价格计算由应用程序完成。"
    )
    if route == "minimal":
        prompt += "\n当前处于预算紧张的简洁模式：只输出必要的执行计划，避免重复解释和冗长思考。"
    if "prompt" in signature.parameters:
        kwargs["prompt"] = prompt
    else:
        kwargs["state_modifier"] = prompt
    graph = create_react_agent(**kwargs)
    context_messages = (
        model_context.to_model_messages()
        if model_context is not None
        else [ContextMessage(role="user", content=query)]
    )
    response = await graph.ainvoke(
        {
            "messages": [
                {"role": message.role, "content": message.content} for message in context_messages
            ]
        },
        config={"recursion_limit": 8},
    )
    messages = response.get("messages", [])
    if not messages:
        return ""
    content = getattr(messages[-1], "content", "")
    usage = getattr(messages[-1], "usage_metadata", None)
    if isinstance(usage, dict):
        record_usage(
            int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0),
            int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0),
        )
    return str(content)[:500]


async def _run_agent_impl(
    request: TaskRequest,
    monitor: Monitor,
    store: PreferenceStore,
    reference_images: list[dict[str, Any]] | None = None,
    data_mode: DataMode | None = None,
    clarification_answers: dict[str, str] | None = None,
    resolved_intent: ShoppingPlan | None = None,
    resolved_query: str | None = None,
    applied_preferences: RememberedPreference | None = None,
    constraint_relaxation_changes: list[ConstraintRelaxationChange] | None = None,
    history_events: Sequence[MonitorEvent] = (),
) -> ShoppingSummaryOutput:
    thread_id = get_thread_id()
    settings = get_settings()
    phase_machine.reset()
    install_default_hooks()
    start_budget(settings.token_budget)
    get_observer().start_trace(
        thread_id,
        query_length=len(request.query),
        data_mode=data_mode or settings.data_mode,
    )
    task_data_mode = data_mode or settings.data_mode
    answers = clarification_answers or {}
    query = resolved_query or strip_memory_commands(
        apply_clarification_context(request.query, answers)
    )
    constraint_relaxations: list[ConstraintRelaxation] = []
    if resolved_intent is None:
        plan = await _call_tool(monitor, "planner", {"query": query}, lambda: planner(query))
        ambiguity = detect_blocking_ambiguity(
            request.query,
            plan,
            resolved_fields=set(answers),
        )
        if ambiguity is not None:
            raise BlockingAmbiguityError(ambiguity)
    else:
        plan, constraint_relaxations = _apply_constraint_relaxations(
            resolved_intent.model_copy(deep=True), constraint_relaxation_changes or []
        )
    if not is_supported_destination(plan.destination):
        raise UnsupportedCapabilityError(
            f"当前仅支持配送至{SUPPORTED_DESTINATION}，暂不支持配送至{plan.destination}。"
        )

    if resolved_intent is None:
        stored_preferences = await store.read_relevant(request.user_id, query, limit=5)
        remembered = RememberedPreference.model_validate(
            {
                field: stored_preferences.get(field, [])
                for field in RememberedPreference.model_fields
            }
        )
        memory_commands = parse_memory_commands(request.query)
        if memory_commands:
            await execute_memory_commands(store, request.user_id, memory_commands)
        remembered = remembered_for_task(remembered, memory_commands)
    else:
        remembered = applied_preferences or RememberedPreference()
        phase_machine.transition("planner_output_ready")

    user_tower_input = UserTowerInput(
        anonymous_shopper_id=request.user_id,
        remembered_preference=remembered.model_copy(deep=True),
    )
    preference_resolution = resolve_preferences(plan, remembered)
    task_overrides = _task_overrides(preference_resolution.decisions)
    effective_remembered = preference_resolution.effective_remembered
    preference_sources = [
        ContextPreferenceSource(
            field=decision.field,
            value=decision.value,
            source="remembered_preference",
        )
        for decision in preference_resolution.decisions
        if decision.status == "applied" and decision.source == "remembered_preference"
    ]
    preference_sources.extend(
        ContextPreferenceSource(field=item.field, value=item.value, source="task_override")
        for item in task_overrides
    )

    await monitor.emit(
        thread_id,
        "intent_resolved",
        message="已保存本次研究的意图和约束",
        data={
            "resolved_query": query,
            "resolved_intent": plan.model_dump(mode="json"),
            "applied_preferences": remembered.model_dump(mode="json"),
            "task_overrides": [item.model_dump(mode="json") for item in task_overrides],
            "constraint_relaxations": [
                item.model_dump(mode="json") for item in constraint_relaxations
            ],
            "data_mode": task_data_mode,
        },
    )

    await monitor.emit(
        thread_id,
        "assistant_call",
        message="Think：正在理解预算、品类和约束",
        data={
            "step": "thinking",
            "preview": query[:160],
            "agent_mode": active_agent_mode(),
            "reference_images": reference_images or [],
            "data_mode": task_data_mode,
        },
    )
    agent_mode = active_agent_mode()
    execution_plan = None
    if requested_mode() == "llm" and agent_mode == "unavailable":
        await monitor.emit(
            thread_id,
            "context_compression",
            data={
                "status": "degraded",
                "reason_code": "model_unavailable",
                "compressed_message_count": 0,
                "retained_message_count": 0,
                "estimated_tokens": 0,
                "summary_fields": [],
            },
        )
        raise RuntimeError("AGENT_MODE=llm but model credentials are not configured")
    if agent_mode == "llm":
        compression_settings = ContextCompressionSettings(
            keep_recent=settings.compress_keep_recent,
            max_tokens=settings.compress_max_tokens,
        )
        fallback_summary = build_context_summary_from_events(
            plan=plan,
            history_events=history_events,
            clarification_answers=answers,
            product_variant=answers.get("product_variant"),
            exact_identity=(
                answers.get("product_variant") if plan.mode == "exact_offer_comparison" else None
            ),
            remembered_preference=effective_remembered,
            task_overrides=task_overrides,
            preference_sources=preference_sources,
        )
        context_messages = build_context_messages(
            query=request.query,
            history_events=history_events,
            clarification_answers=answers,
        )
        try:
            try:
                model_context = build_model_context(
                    query=request.query,
                    plan=plan,
                    history_events=history_events,
                    clarification_answers=answers,
                    product_variant=answers.get("product_variant"),
                    remembered_preference=effective_remembered,
                    task_overrides=task_overrides,
                    preference_sources=preference_sources,
                    settings=compression_settings,
                )
            except asyncio.TimeoutError:
                model_context = safe_bounded_context(
                    context_messages,
                    fallback_summary,
                    compression_settings,
                    reason_code="compression_timeout",
                )
            except Exception:  # noqa: BLE001 - model context must fail closed to a bounded window
                model_context = safe_bounded_context(
                    context_messages,
                    fallback_summary,
                    compression_settings,
                    reason_code="compression_failed",
                )
            await monitor.emit(
                thread_id,
                "context_compression",
                data=model_context.compression_event_data(),
            )
            advisory_args: tuple[Any, ...] = (
                query,
                effective_remembered.model_dump(mode="json"),
                model_context,
            )
            advisory_kwargs: dict[str, Any] = {}
            if "user_id" in inspect.signature(_run_react_advisory).parameters:
                advisory_kwargs["user_id"] = request.user_id
            preview = await _run_react_advisory(*advisory_args, **advisory_kwargs)
            await monitor.emit(
                thread_id,
                "assistant_call",
                message="LangGraph ReAct 已完成意图分析",
                data={
                    "step": "thinking",
                    "preview": preview,
                    "source": "live",
                    "token_route": budget_state().route,
                    "data_mode": task_data_mode,
                },
            )
            execution_plan = get_execution_plan()
            if execution_plan is not None:
                await monitor.emit(
                    thread_id,
                    "assistant_call",
                    message="模型已提出受限执行计划，应用将继续做确定性校验",
                    data={
                        "step": "planning",
                        "platforms": list(execution_plan.platforms),
                        "fork": execution_plan.fork,
                        "steps": list(execution_plan.steps),
                        "source": execution_plan.source,
                        "data_mode": task_data_mode,
                    },
                )
        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError):
                raise
            if not allow_rules_fallback():
                raise
            await monitor.emit(
                thread_id,
                "context_compression",
                data={
                    "status": "degraded",
                    "reason_code": (
                        "model_timeout"
                        if isinstance(exc, asyncio.TimeoutError)
                        else "model_unavailable"
                    ),
                    "compressed_message_count": 0,
                    "retained_message_count": 0,
                    "estimated_tokens": 0,
                    "summary_fields": [],
                },
            )
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
        if budget_state().route == "fallback":
            await monitor.emit(
                thread_id,
                "context_compression",
                data={
                    "status": "degraded",
                    "reason_code": "token_budget_fallback",
                    "compressed_message_count": 0,
                    "retained_message_count": 0,
                    "estimated_tokens": 0,
                    "summary_fields": [],
                },
            )
            await monitor.emit(
                thread_id,
                "assistant_call",
                message="Token 预算已用尽，切换规则编排",
                data={
                    "step": "thinking",
                    "source": "computed",
                    "token_route": "fallback",
                    "data_mode": task_data_mode,
                },
            )

    selected_platforms = (
        execution_plan.platforms
        if execution_plan is not None and execution_plan.platforms
        else settings.enabled_marketplaces
    )
    await monitor.emit(
        thread_id,
        "assistant_call",
        message=f"Act：检索 {len(selected_platforms)} 个已选平台，并准备类目召回",
        data={
            "step": "acting",
            "category": plan.category,
            "platforms": list(selected_platforms),
            "data_mode": task_data_mode,
        },
    )
    platforms = [cast(Platform, name) for name in selected_platforms]

    insight_task = asyncio.create_task(
        _call_tool(
            monitor,
            "category_insight",
            {"category": plan.category, "depth": "quick"},
            lambda: _category_insight_with_fallback(plan.category),
            event_thread_id=thread_id,
            data_mode=task_data_mode,
        )
    )

    async def search_branch(demand: dict[str, Any]) -> ItemSearchOutput:
        platform: Platform = demand["platform"]
        channel = next(item for item in settings.marketplaces if item.name == platform)
        failure_metadata = ProviderMetadata(
            source="fixture" if task_data_mode == "sandbox" else "live",
            provider=channel.provider,
            status="unavailable",
            fallback_reason="provider request failed: unexpected exception",
            failure_reason="request_failed",
        )
        try:
            return await _call_tool(
                monitor,
                "item_search",
                {"platform": platform, "top_k": 20},
                lambda: item_search(query, platform, top_k=20, user_id=request.user_id),
                event_thread_id=thread_id,
                data_mode=task_data_mode,
                failure_metadata=failure_metadata,
                observer_thread_id=get_thread_id(),
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

    try:
        demands = [{"platform": platform, "query": query} for platform in platforms]
        if execution_plan is not None and not execution_plan.fork:
            searches = [await search_branch(demand) for demand in demands]
        else:
            searches = await dispatch_tool(
                demands,
                search_branch,
                monitor,
                data_mode=task_data_mode,
            )
    except BaseException:
        if not insight_task.done():
            insight_task.cancel()
        await asyncio.gather(insight_task, return_exceptions=True)
        raise
    insight = await insight_task
    candidates = [candidate for result in searches for candidate in result.candidates]
    providers = {result.platform: result.provider for result in searches}
    if task_data_mode != "sandbox":
        for platform in MARKETPLACES:
            if platform in providers:
                continue
            channel = next(item for item in settings.marketplaces if item.name == platform)
            providers[platform] = ProviderMetadata(
                source="live",
                provider=channel.provider,
                status="unavailable",
                fallback_reason=(
                    f"{platform.upper()}_DATA_CHANNEL_ENDPOINT and "
                    f"{platform.upper()}_DATA_CHANNEL_CREDENTIAL are not fully configured "
                    f"(legacy aliases {platform.upper()}_API_ENDPOINT and "
                    f"{platform.upper()}_API_KEY are supported)"
                ),
                failure_reason="not_configured",
            )

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

    recall = await _call_tool(
        monitor,
        "recall",
        {"candidate_count": len(candidates), "top_k": 20},
        lambda: recall_orchestrator.recall(
            query,
            candidates,
            category_insight=insight,
            top_k=20,
            user_input=user_tower_input,
        ),
        event_thread_id=thread_id,
        data_mode=task_data_mode,
    )
    assert isinstance(recall, RecallResult)
    recalled_candidates = recall.candidates
    await monitor.emit(
        thread_id,
        "assistant_call",
        message="Recall：已根据实际可用 channel 选择或排序 Product Evidence",
        data={
            "step": "recalling",
            "candidate_count": len(candidates),
            "selected_count": len(recalled_candidates),
            "recall_provenance": recall.provenance.model_dump(mode="json"),
            "data_mode": task_data_mode,
        },
    )

    prices = await _call_tool(
        monitor,
        "price_compare",
        {"candidate_count": len(recalled_candidates), "base_currency": "CNY"},
        lambda: price_compare(recalled_candidates),
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
        lambda: item_picker(shipping, plan, remembered_preferences=remembered),
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
            picks.matching_offers,
            providers=providers,
            rate_source=prices.rate_source,
            rates_as_of=prices.rates_as_of,
            exchange_rate=prices.exchange_rate,
            excluded_currencies=prices.excluded_currencies,
            calculation_exclusions=prices.calculation_exclusions,
            shipping_basis=shipping.calculation_basis,
            unavailable_marketplaces=[
                name
                for name, metadata in providers.items()
                if metadata.status == "unavailable" or metadata.failure_reason is not None
            ],
            data_mode=task_data_mode,
            preference_decisions=picks.preference_decisions,
            resolved_query=query,
            resolved_intent=plan,
            applied_preferences=remembered,
            task_overrides=task_overrides,
            constraint_relaxations=constraint_relaxations,
            product_evidence=candidates,
            recall_provenance=recall.provenance,
        ),
    )
    return result


async def run_agent(
    request: TaskRequest,
    monitor: Monitor,
    store: PreferenceStore,
    reference_images: list[dict[str, Any]] | None = None,
    data_mode: DataMode | None = None,
    clarification_answers: dict[str, str] | None = None,
    resolved_intent: ShoppingPlan | None = None,
    resolved_query: str | None = None,
    applied_preferences: RememberedPreference | None = None,
    constraint_relaxation_changes: list[ConstraintRelaxationChange] | None = None,
    history_events: Sequence[MonitorEvent] = (),
) -> ShoppingSummaryOutput:
    """Run one task inside the complete Harness lifecycle."""

    thread_id = get_thread_id()
    phase_machine.reset()
    install_default_hooks()
    session_context = await harness.run(
        "on_session_start",
        {
            "thread_id": thread_id,
            "user_id": request.user_id,
            "query": request.query,
            "data_mode": data_mode or get_settings().data_mode,
        },
    )
    await harness.run(
        "pre_think",
        {
            **session_context,
            "step": "thinking",
            "tool_history": phase_machine.tool_history(),
        },
    )
    result: ShoppingSummaryOutput | None = None
    failure: BaseException | None = None
    try:
        result = await _run_agent_impl(
            request,
            monitor,
            store,
            reference_images=reference_images,
            data_mode=data_mode,
            clarification_answers=clarification_answers,
            resolved_intent=resolved_intent,
            resolved_query=resolved_query,
            applied_preferences=applied_preferences,
            constraint_relaxation_changes=constraint_relaxation_changes,
            history_events=history_events,
        )
        return result
    except BaseException as exc:
        failure = exc
        raise
    finally:
        await harness.run(
            "on_session_end",
            {
                **session_context,
                "result": result,
                "error": type(failure).__name__ if failure is not None else None,
                "tool_history": phase_machine.tool_history(),
            },
        )
