from __future__ import annotations

import json

from app.schemas import (
    CalculationExclusion,
    Candidate,
    ConstraintRelaxation,
    DataMode,
    ExchangeRateProvenance,
    FileLink,
    ItemPickerOutput,
    LandedCost,
    PreferenceDecision,
    ProviderMetadata,
    RememberedPreference,
    ShoppingPlan,
    ShoppingSummaryOutput,
    TaskOverride,
)
from app.utils.thread_ctx import get_session_dir, get_thread_id


def _rate_label(value: str) -> str:
    return {
        "reference-table": "内置参考汇率表",
        "configured": "自定义汇率配置",
        "unspecified": "未标注来源",
    }.get(value, value)


def _date_label(value: str) -> str:
    return "未标注日期" if value == "unspecified" else value


async def shopping_summary(
    picks: ItemPickerOutput,
    comparison: list[LandedCost],
    providers: dict[str, ProviderMetadata] | None = None,
    rate_source: str = "unspecified",
    rates_as_of: str = "unspecified",
    exchange_rate: ExchangeRateProvenance | None = None,
    excluded_currencies: list[str] | None = None,
    calculation_exclusions: list[CalculationExclusion] | None = None,
    shipping_basis: str = "estimated; verify at checkout",
    unavailable_marketplaces: list[str] | None = None,
    data_mode: DataMode | None = None,
    preference_decisions: list[PreferenceDecision] | None = None,
    resolved_query: str | None = None,
    resolved_intent: ShoppingPlan | None = None,
    applied_preferences: RememberedPreference | None = None,
    task_overrides: list[TaskOverride] | None = None,
    constraint_relaxations: list[ConstraintRelaxation] | None = None,
    product_evidence: list[Candidate] | None = None,
) -> ShoppingSummaryOutput:
    """Create the terminal result and persist Markdown and JSON reports."""

    thread_id = get_thread_id()
    directory = get_session_dir()
    provider_details = providers or {}
    exchange_rate = exchange_rate or ExchangeRateProvenance(
        source=rate_source,
        effective_date=rates_as_of,
    )
    calculation_exclusions = calculation_exclusions or []
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
    excluded_notice = "部分候选已排除；" if calculation_exclusions or excluded_currencies else ""
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
    calculation_notice = (
        f"比较货币：CNY；汇率来源：{_rate_label(exchange_rate.source)}；"
        f"effective date：{_date_label(exchange_rate.effective_date)}；"
        f"calculation basis：{exchange_rate.calculation_basis}；{excluded_notice}"
        f"运费、税费与时效均为估算（{shipping_basis}）；这不是 checkout guarantee，"
        "购买前请以平台结算页为准。"
    )
    mode_label = (
        "Exact Offer Comparison" if picks.mode == "exact_offer_comparison" else "Product Research"
    )
    preference_labels = {
        "applied": "应用",
        "ignored": "忽略",
        "overridden": "覆盖",
    }

    def preference_rationale() -> str:
        if not preference_decisions:
            return "偏好处理：本任务没有可应用的 Remembered Preference 或显式软偏好。"
        grouped: dict[str, list[str]] = {"applied": [], "ignored": [], "overridden": []}
        for decision in preference_decisions:
            source = "当前请求" if decision.source == "current_request" else "Remembered Preference"
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
                (
                    f"{mode_label} 只保留 Identity Evidence 证明同款的 Matching Offer，"
                    f"按{ranking_basis}和硬性约束筛选："
                )
            ]
        else:
            lines = [f"{mode_label} 按{ranking_basis}、硬性约束和可核验证据筛选了以下选择："]
        for item in picks.recommendations:
            lines.append(f"{item.rank}. {item.title}：{item.reason}")
        if picks.alternative_candidates:
            lines.append(
                f"另有 {len(picks.alternative_candidates)} 个 Alternative Candidate，"
                "因 Identity Evidence 不足未参与正式排名。"
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
            f"{mode_label} 没有 Identity Evidence 充分的 Matching Offer；"
            f"{len(picks.alternative_candidates)} 个相似商品已单列为 Alternative Candidate，"
            f"未参与正式排名或最低价结论。\n{rationale}"
        )
    elif picks.exclusions or comparison:
        final_answer = (
            f"当前没有满足全部硬性条件的候选。可以查看排除原因，并在确认后放宽条件。\n{rationale}"
        )
    elif calculation_exclusions:
        final_answer = (
            "候选商品均未能完成合法的价格计算；请查看计算排除原因并检查金额或汇率配置。\n"
            f"{rationale}"
        )
    else:
        final_answer = f"已启用平台没有返回可比较的候选商品。请调整关键词后重试。\n{rationale}"

    markdown_lines = [
        "# Shopping Agent 购物研究报告",
        "",
        final_answer,
        "",
        f"## Research Mode\n\n{mode_label}（{picks.mode}）",
        "",
        f"> {calculation_notice}",
        "",
        "## Matching Offer / 到手价比较",
        "",
        "| 平台 | 商品 | 商品价(原币) | 商品价(CNY) | 运费估算 | 关税估算 | 到手价 | 时效估算 | 来源 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in comparison:
        markdown_lines.append(
            f"| {item.platform} | {item.title} | {item.currency} {item.price:.2f} | "
            f"{item.price_cny:.2f} | {item.shipping_cny:.2f}（估算） | "
            f"{item.duty_cny:.2f}（估算） | {item.landed_cny:.2f} | "
            f"{item.eta_days}天（估算） | {item.source} |"
        )
    markdown_lines.extend(
        [
            "",
            "## Ranking Profile",
            "",
            "优先级：" + " > ".join(picks.ranking_profile.priority_order),
            "显式表达：" + ("是" if picks.ranking_profile.explicit else "否（默认以到手价优先）"),
        ]
    )
    markdown_lines.extend(["", "## 偏好处理", ""])
    if preference_decisions:
        markdown_lines.extend(
            f"- {preference_labels[item.status]}：{item.value}；"
            f"来源：{'当前请求' if item.source == 'current_request' else 'Remembered Preference'}；"
            f"{item.reason}"
            for item in preference_decisions
        )
    else:
        markdown_lines.append("- 本任务没有可应用的 Remembered Preference 或显式软偏好。")
    if calculation_exclusions:
        markdown_lines.extend(["", "## 计算排除", ""])
        markdown_lines.extend(
            f"- {item.title}（{item.item_id}）：{item.reason_code}，{item.reason}"
            for item in calculation_exclusions
        )
    if picks.alternative_candidates:
        markdown_lines.extend(["", "## Alternative Candidate", ""])
        for candidate in picks.alternative_candidates:
            evidence = candidate.identity_evidence
            markdown_lines.append(
                f"- {candidate.title}（{candidate.platform}）：{candidate.reason}"
            )
            markdown_lines.append(
                f"  - Identity Evidence：{evidence.basis}；"
                f"matched={', '.join(evidence.matched_fields) or '-'}；"
                f"missing={', '.join(evidence.missing_fields) or '-'}；"
                f"conflicting={', '.join(evidence.conflicting_fields) or '-'}"
            )
    if picks.working_assumptions:
        markdown_lines.extend(["", "## 工作假设", ""])
        markdown_lines.extend(
            f"- {assumption.field}：{assumption.value}。{assumption.reason}"
            for assumption in picks.working_assumptions
        )
    if picks.unverified_candidates:
        markdown_lines.extend(["", "## 未验证候选", ""])
        for candidate in picks.unverified_candidates:
            markdown_lines.append(f"- {candidate.title}：{candidate.reason}")
            for evaluation in candidate.constraint_evaluations:
                if evaluation.status == "unknown":
                    markdown_lines.append(
                        f"  - {evaluation.constraint.label}：{evaluation.reason_code}"
                    )
    if picks.exclusions:
        markdown_lines.extend(["", "## 排除项", ""])
        for exclusion in picks.exclusions:
            reasons = "；".join(
                f"{item.constraint.label}（{item.reason_code}）"
                for item in exclusion.violated_constraints
            )
            markdown_lines.append(f"- {exclusion.title}：{reasons}")
    if picks.relaxation_suggestions:
        markdown_lines.extend(["", "## 约束放宽建议", ""])
        markdown_lines.extend(f"- {item.suggestion}" for item in picks.relaxation_suggestions)
    if provider_details:
        markdown_lines.extend(
            [
                "",
                "## 数据提供方",
                "",
                "| 平台 | 状态 | 数据类型 | 稳定失败原因 | 说明 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for name, metadata in provider_details.items():
            markdown_lines.append(
                f"| {name} | {metadata.status} | {metadata.source} | "
                f"{metadata.failure_reason or '-'} | {metadata.fallback_reason or '-'} |"
            )
    markdown_path = directory / "shopping-report.md"
    json_path = directory / "shopping-report.json"
    markdown_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

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
        files=[
            FileLink(name=markdown_path.name, url=f"/api/files/{thread_id}/{markdown_path.name}"),
            FileLink(name=json_path.name, url=f"/api/files/{thread_id}/{json_path.name}"),
        ],
        provider_mode=provider_mode,
        providers=provider_details,
        calculation_notice=calculation_notice,
        exchange_rate=exchange_rate,
        calculation_exclusions=calculation_exclusions,
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
    )
    json_path.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result
