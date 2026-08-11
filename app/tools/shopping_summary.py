from __future__ import annotations

from app.schemas import (
    CalculationExclusion,
    Candidate,
    ConstraintRelaxation,
    DataMode,
    ExchangeRateProvenance,
    ItemPickerOutput,
    LandedCost,
    PreferenceDecision,
    ProviderMetadata,
    RecallProvenance,
    RememberedPreference,
    ShippingCalculationExclusion,
    ShoppingPlan,
    ShoppingSummaryOutput,
    TaskOverride,
    TaxCalculationExclusion,
)
from app.security import audit_output
from app.utils.thread_ctx import get_thread_id


def _rate_label(value: str) -> str:
    return {
        "offer-level-quotes": "逐商品数据商换算报价",
        "native-CNY": "商品原币为人民币，无需换算",
        "unspecified": "未标注来源",
    }.get(value, value)


def _date_label(value: str) -> str:
    return "未标注日期" if value == "unspecified" else value


def _calculation_basis_label(value: str) -> str:
    return {
        "original_amount * rate_to_cny": "原币金额 × 人民币汇率",
        "not provided": "未提供",
    }.get(value, value)


async def shopping_summary(
    picks: ItemPickerOutput,
    comparison: list[LandedCost],
    providers: dict[str, ProviderMetadata] | None = None,
    rate_source: str = "unspecified",
    rates_as_of: str = "unspecified",
    exchange_rate: ExchangeRateProvenance | None = None,
    excluded_currencies: list[str] | None = None,
    calculation_exclusions: list[CalculationExclusion] | None = None,
    shipping_exclusions: list[ShippingCalculationExclusion] | None = None,
    tax_exclusions: list[TaxCalculationExclusion] | None = None,
    shipping_basis: str = "使用数据通道返回的线路与服务报价",
    unavailable_marketplaces: list[str] | None = None,
    data_mode: DataMode | None = None,
    preference_decisions: list[PreferenceDecision] | None = None,
    resolved_query: str | None = None,
    resolved_intent: ShoppingPlan | None = None,
    applied_preferences: RememberedPreference | None = None,
    task_overrides: list[TaskOverride] | None = None,
    constraint_relaxations: list[ConstraintRelaxation] | None = None,
    product_evidence: list[Candidate] | None = None,
    recall_provenance: RecallProvenance | None = None,
) -> ShoppingSummaryOutput:
    """Create the typed terminal result; the API materializes reports from its snapshot."""

    thread_id = get_thread_id()
    provider_details = providers or {}
    exchange_rate = exchange_rate or ExchangeRateProvenance(
        source=rate_source,
        effective_date=rates_as_of,
    )
    calculation_exclusions = calculation_exclusions or []
    shipping_exclusions = shipping_exclusions or []
    tax_exclusions = tax_exclusions or []
    preference_decisions = preference_decisions or []
    task_overrides = task_overrides or []
    constraint_relaxations = constraint_relaxations or []
    product_evidence = product_evidence or []
    applied_preferences = applied_preferences or RememberedPreference()
    sources = {item.source for item in comparison}
    sources.update(metadata.source for metadata in provider_details.values())
    if data_mode is not None:
        provider_mode = data_mode
    elif sources == {"fixture"}:
        provider_mode = "sandbox"
    elif "fixture" in sources:
        provider_mode = "mixed"
    else:
        provider_mode = "live"
    unavailable = sorted(
        set(unavailable_marketplaces or [])
        | {
            name
            for name, metadata in provider_details.items()
            if metadata.status == "unavailable" or metadata.failure_reason is not None
        }
    )
    result_kind = (
        "sandbox"
        if provider_mode == "sandbox" and not unavailable
        else "partial"
        if unavailable or provider_mode == "mixed"
        else "live"
    )
    exclusion_notices: list[str] = []
    if calculation_exclusions or excluded_currencies:
        exclusion_notices.append(f"价格换算排除 {len(calculation_exclusions)} 个候选")
    if shipping_exclusions:
        exclusion_notices.append(f"运费报价证据不足排除 {len(shipping_exclusions)} 个候选")
    if tax_exclusions:
        exclusion_notices.append(f"税务证据不足排除 {len(tax_exclusions)} 个候选")
    excluded_notice = "；".join(exclusion_notices)
    if excluded_notice:
        excluded_notice += "；"
    priority_labels = {
        "landed_cost": "到手价",
        "preference_match": "偏好匹配",
        "evidence_quality": "证据质量",
        "delivery_time": "配送时效",
    }
    ranking_basis = "、".join(
        priority_labels.get(dimension, dimension)
        for dimension in picks.ranking_profile.priority_order
    )
    provider_label = "、".join(exchange_rate.providers) if exchange_rate.providers else "无"
    calculation_notice = (
        f"比较货币：CNY；汇率依据：{_rate_label(exchange_rate.source)}；"
        f"数据商：{provider_label}；最新观测时间：{_date_label(exchange_rate.effective_date)}；"
        f"计算方式：{_calculation_basis_label(exchange_rate.calculation_basis)}；{excluded_notice}"
        f"运费按线路、服务和 quote 计算，税费与时效仍为研究时点估算（{shipping_basis}）；"
        f"{exchange_rate.settlement_notice} 购买前请以平台结算页、承运商及海关核定为准。"
    )
    mode_label = "同款商品比价" if picks.mode == "exact_offer_comparison" else "不同商品推荐"
    preference_labels = {
        "applied": "应用",
        "ignored": "忽略",
        "overridden": "覆盖",
    }

    def preference_rationale() -> str:
        if not preference_decisions:
            return "偏好处理：本次研究没有使用已保存的偏好或其他偏好条件。"
        grouped: dict[str, list[str]] = {"applied": [], "ignored": [], "overridden": []}
        for decision in preference_decisions:
            source = "当前请求" if decision.source == "current_request" else "已保存的偏好"
            grouped[decision.status].append(f"{decision.value}（{source}）")
        parts = [
            f"{preference_labels[status]}：{'、'.join(values)}"
            for status, values in grouped.items()
            if values
        ]
        return "偏好处理：" + "；".join(parts) + "。"

    rationale = preference_rationale()
    if picks.recommendations:
        if picks.mode == "exact_offer_comparison":
            lines = [
                (f"{mode_label}只保留证据能够证明为同款的报价，按{ranking_basis}和必要条件筛选：")
            ]
        else:
            lines = [f"{mode_label}按{ranking_basis}、必要条件和可核验证据筛选了以下选择："]
        for item in picks.recommendations:
            lines.append(f"{item.rank}. {item.title}：{item.reason}")
        if picks.alternative_candidates:
            lines.append(
                f"另有 {len(picks.alternative_candidates)} 个相似商品候选，"
                "因同款证据不足未参与正式排名。"
            )
        if unavailable:
            lines.append(
                "部分平台不可用："
                + "；".join(
                    f"{name}（{provider_details[name].failure_reason or 'unavailable'}）"
                    for name in unavailable
                    if name in provider_details
                )
            )
        if tax_exclusions:
            lines.append(
                f"另有 {len(tax_exclusions)} 个候选因缺少可核验税务证据，"
                "未参与精确到手价计算和排名。"
            )
        if shipping_exclusions:
            lines.append(
                f"另有 {len(shipping_exclusions)} 个候选因缺少有效运费报价，"
                "未参与精确到手价计算和排名。"
            )
        lines.append(rationale)
        final_answer = "\n".join(lines)
    elif picks.unverified_candidates:
        final_answer = (
            f"当前没有可验证的推荐；{len(picks.unverified_candidates)} 个候选缺少硬性条件证据。"
            "补充商品材质或规格证据后再判断。\n"
            f"{rationale}"
        )
    elif picks.alternative_candidates and not picks.matching_offers:
        final_answer = (
            f"{mode_label}没有证据充分的同款报价；"
            f"{len(picks.alternative_candidates)} 个相似商品候选已单独列出，"
            f"未参与正式排名或最低价结论。\n{rationale}"
        )
    elif picks.exclusions or comparison:
        final_answer = (
            f"当前没有满足全部硬性条件的候选。可以查看排除原因，并在确认后放宽条件。\n{rationale}"
        )
    elif calculation_exclusions:
        final_answer = (
            "候选商品均未能完成合法的价格计算；请查看计算排除原因并检查原始金额或商品级汇率报价证据。\n"
            f"{rationale}"
        )
    elif shipping_exclusions:
        final_answer = (
            f"{len(shipping_exclusions)} 个候选缺少面向中国大陆的有效运费报价，"
            "无法生成可比较的到手价；请让数据通道补充线路、服务、费用、时效与有效期。\n"
            f"{rationale}"
        )
    elif tax_exclusions:
        final_answer = (
            f"{len(tax_exclusions)} 个候选缺少可核验税务证据，无法生成真实到手价；"
            "请让数据通道补充 HS Code、原产地、进口模式、税率来源和生效日期。\n"
            f"{rationale}"
        )
    else:
        final_answer = f"已启用平台没有返回可比较的候选商品。请调整关键词后重试。\n{rationale}"

    _, final_answer = audit_output(final_answer)

    result = ShoppingSummaryOutput(
        thread_id=thread_id,
        final_answer=final_answer,
        resolved_query=resolved_query,
        resolved_intent=resolved_intent,
        applied_preferences=applied_preferences,
        task_overrides=task_overrides,
        constraint_relaxations=constraint_relaxations,
        product_evidence=product_evidence,
        recommendations=picks.recommendations,
        comparison=comparison,
        mode=picks.mode,
        matching_offers=picks.matching_offers,
        alternative_candidates=picks.alternative_candidates,
        files=[],
        provider_mode=provider_mode,
        providers=provider_details,
        calculation_notice=calculation_notice,
        exchange_rate=exchange_rate,
        calculation_exclusions=calculation_exclusions,
        shipping_exclusions=shipping_exclusions,
        tax_exclusions=tax_exclusions,
        ranking_profile=picks.ranking_profile,
        data_mode=provider_mode,
        result_kind=result_kind,
        unavailable_marketplaces=unavailable,
        unverified_candidates=picks.unverified_candidates,
        exclusions=picks.exclusions,
        working_assumptions=picks.working_assumptions,
        relaxation_suggestions=picks.relaxation_suggestions,
        match_status=picks.match_status,
        preference_decisions=preference_decisions,
        recall_provenance=recall_provenance,
    )
    return result
