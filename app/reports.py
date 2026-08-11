from __future__ import annotations

import html
import io
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.schemas import (
    Candidate,
    FileLink,
    LandedCost,
    ProviderMetadata,
    ReportFormat,
    ReportNotice,
    ResearchReportSnapshot,
    TaskSnapshot,
)


@dataclass(frozen=True, slots=True)
class ReportFileSpec:
    format: ReportFormat
    name: str
    content_type: str


REPORT_FILE_SPECS = (
    ReportFileSpec("markdown", "shopping-report.md", "text/markdown; charset=utf-8"),
    ReportFileSpec("json", "shopping-report.json", "application/json; charset=utf-8"),
    ReportFileSpec("pdf", "shopping-report.pdf", "application/pdf"),
)
ESTIMATE_BOUNDARY = (
    "运费、进口税费与配送时效均为估算；这不是 checkout guarantee。"
    "购买前请以平台结算页或海关核定为准。"
)
CUSTOMER_ESTIMATE_BOUNDARY = (
    "人民币到手价使用本次研究时的商品汇率、线路运费和税率证据估算；"
    "最终商品价、支付汇率与费用、承运费用、进口税费和配送时效，"
    "请以平台结算页、发卡行、承运商及海关核定为准。"
)
PLATFORM_LABELS = {
    "amazon": "Amazon",
    "shopee": "Shopee",
    "aliexpress": "AliExpress",
    "ebay": "eBay",
}
PROVIDER_SOURCE_LABELS = {
    "live": "数据提供商通道",
    "curated": "采买数据",
    "fixture": "演示数据",
    "computed": "系统计算",
}
PROVIDER_STATUS_LABELS = {
    "ok": "可用",
    "degraded": "部分可用",
    "unavailable": "不可用",
}
PROVIDER_FAILURE_LABELS = {
    "not_configured": "未配置数据通道",
    "request_failed": "数据通道请求失败",
    "empty_response": "本次没有返回商品",
    "sandbox_forbidden": "当前环境禁止使用演示数据",
    "circuit_open": "数据通道暂时不可用",
}
RANKING_LABELS = {
    "landed_cost": "到手价",
    "preference_match": "需求匹配",
    "evidence_quality": "信息可信度",
    "delivery_time": "配送时效",
}
IMPORT_REGIME_LABELS = {
    "general_trade": "一般贸易",
    "cross_border_ecommerce": "跨境电商零售进口",
    "personal_postal": "个人邮递物品",
    "seller_collected": "卖家已代收",
}
ATTRIBUTE_LABELS = {
    "material": "材质",
    "style": "风格",
    "weight_kg": "重量",
    "color": "颜色",
    "waterproof": "防水",
}
ASSUMPTION_FIELD_LABELS = {
    **ATTRIBUTE_LABELS,
    "destination": "配送地区",
    "category": "商品类别",
}
PREFERENCE_FIELD_LABELS = {
    "material_preferences": "材质偏好",
    "style_preferences": "风格偏好",
    "soft_preferences": "其他偏好",
    "avoid": "明确避开",
}


class ReportGenerationError(RuntimeError):
    """A completed snapshot could not be rendered into its deterministic artifacts."""


def report_file_links(snapshot: TaskSnapshot) -> list[FileLink]:
    return [
        FileLink(
            file_id=f"{snapshot.snapshot_id}:{spec.format}",
            format=spec.format,
            name=spec.name,
            url=f"/api/files/{snapshot.thread_id}/{spec.name}",
            content_type=spec.content_type,
        )
        for spec in REPORT_FILE_SPECS
    ]


def _lineage_message(report: ResearchReportSnapshot) -> str:
    lineage = report.lineage
    if lineage is None:
        return "Lineage：root snapshot；没有 parent snapshot。"
    relation = "Research Rerun" if lineage.relation == "rerun" else "Constraint Relaxation"
    return (
        f"Lineage：{relation}；parent snapshot={lineage.parent_snapshot_id}；"
        f"root snapshot={lineage.root_snapshot_id}；depth={lineage.depth}。"
    )


def _unavailable_message(name: str, metadata: ProviderMetadata) -> str:
    reason = metadata.failure_reason or "unavailable"
    detail = metadata.fallback_reason or "未提供额外失败说明"
    return f"{name} marketplace unavailable：reason={reason}；{detail}。"


def _build_notices(report: ResearchReportSnapshot) -> list[ReportNotice]:
    notices: list[ReportNotice] = [
        ReportNotice(
            code="snapshot_effective_time",
            message=(
                f"Research Snapshot effective time：{report.snapshot_effective_at}。"
                "这是不可变的历史快照，不代表当前市场状态。"
            ),
        ),
        ReportNotice(code="lineage", message=_lineage_message(report)),
        ReportNotice(code="calculation", message=report.calculation_notice),
        ReportNotice(code="estimate_boundary", message=ESTIMATE_BOUNDARY),
    ]
    if report.data_mode == "mixed":
        notices.append(
            ReportNotice(
                code="mixed_mode",
                message="data_mode=mixed；Live Result 与 Sandbox Result 的来源边界已在结果中保留。",
            )
        )
    if report.result_kind == "partial":
        unavailable = report.unavailable_marketplaces or sorted(
            name
            for name, metadata in report.providers.items()
            if metadata.status == "unavailable" or metadata.failure_reason is not None
        )
        notices.append(
            ReportNotice(
                code="partial_result",
                message=(
                    "Partial Result：仅使用返回可用 Product Evidence 的 marketplace；"
                    f"不可用平台={', '.join(unavailable) or '未标注'}。"
                ),
            )
        )
        for name in unavailable:
            metadata = report.providers.get(name)
            if metadata is not None:
                notices.append(
                    ReportNotice(
                        code="unavailable_marketplace",
                        message=_unavailable_message(name, metadata),
                    )
                )
    if report.match_status == "no_match":
        notices.append(
            ReportNotice(
                code="no_match",
                message=(
                    "No-Match：没有 Verified Candidate 同时满足当前 Shopping Research Task 的全部 Hard Constraint。"
                ),
            )
        )
    if report.unverified_candidates:
        notices.append(
            ReportNotice(
                code="unverified_candidate",
                message=(
                    f"Unverified Candidate：{len(report.unverified_candidates)} 个候选缺少至少一项 Hard Constraint 证据，"
                    "不参与推荐排名。"
                ),
            )
        )
    if report.alternative_candidates:
        notices.append(
            ReportNotice(
                code="alternative_candidate",
                message=(
                    f"Alternative Candidate：{len(report.alternative_candidates)} 个候选未被证明为目标 Product Variant，"
                    "不参与 Exact Offer Comparison 排名。"
                ),
            )
        )
    if report.recall_provenance is not None:
        degraded = [
            f"{name}={channel.reason_code}"
            for name, channel in report.recall_provenance.channels.items()
            if channel.state in {"degraded", "unavailable"}
        ]
        notices.append(
            ReportNotice(
                code="recall_provenance",
                message=(
                    f"Recall mode={report.recall_provenance.mode}；"
                    f"participating channels={','.join(report.recall_provenance.participating_channels) or 'none'}；"
                    f"degraded reasons={','.join(degraded) or 'none'}。"
                ),
            )
        )
        personalization = report.recall_provenance.personalization
        if personalization is not None:
            fields = ",".join(personalization.preference_fields) or "none"
            values = ",".join(personalization.preference_values) or "none"
            notices.append(
                ReportNotice(
                    code="personalization_provenance",
                    message=(
                        f"Personalization state={personalization.state}；"
                        f"input source={personalization.input_source}；"
                        f"preference fields={fields}；values={values}；"
                        f"signal={personalization.signal}；participated={personalization.participated}；"
                        f"reason={personalization.reason_code}。"
                        "Anonymous Shopper ID 仅用于关联研究与显式 Remembered Preference，"
                        "不是登录账号、认证身份或数据所有权证明。"
                    ),
                )
            )
    if (
        report.exclusions
        or report.calculation_exclusions
        or report.shipping_exclusions
        or report.tax_exclusions
    ):
        notices.append(
            ReportNotice(
                code="exclusion",
                message=(
                    f"筛选说明：{len(report.exclusions)} 个候选未满足必要条件、"
                    f"{len(report.calculation_exclusions)} 个候选缺少有效价格换算、"
                    f"{len(report.shipping_exclusions)} 个候选缺少有效运费报价、"
                    f"{len(report.tax_exclusions)} 个候选缺少税务证据；均已单列且未进入推荐排名。"
                ),
            )
        )
    return notices


def build_report_snapshot(
    snapshot: TaskSnapshot,
    files: list[FileLink] | None = None,
) -> ResearchReportSnapshot:
    if snapshot.status != "completed" or snapshot.result is None:
        raise ReportGenerationError("reports require a completed Research Snapshot")
    result = snapshot.result
    payload = result.model_dump(mode="python")
    payload.update(
        {
            "thread_id": snapshot.thread_id,
            "resolved_query": snapshot.resolved_query or result.resolved_query,
            "resolved_intent": snapshot.resolved_intent or result.resolved_intent,
            "mode": snapshot.mode or result.mode,
            "working_assumptions": snapshot.working_assumptions or result.working_assumptions,
            "applied_preferences": snapshot.applied_preferences,
            "task_overrides": snapshot.task_overrides,
            "constraint_relaxations": snapshot.constraint_relaxations,
            "product_evidence": snapshot.product_evidence or result.product_evidence,
            "providers": snapshot.provider_coverage or result.providers,
            "exchange_rate": snapshot.exchange_rate or result.exchange_rate,
            "files": files or result.files,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_effective_at": snapshot.updated_at,
            "snapshot_created_at": snapshot.created_at,
            "user_id": snapshot.user_id,
            "query": snapshot.query,
            "lineage": snapshot.lineage,
            "notices": [],
        }
    )
    report = ResearchReportSnapshot.model_validate(payload)
    return report.model_copy(update={"notices": _build_notices(report)})


def _markdown_cell(value: Any) -> str:
    text = html.escape(str(value), quote=False).replace("\n", " ").strip()
    for character in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(character, f"\\{character}")
    return text


def _markdown_item_link(item: Candidate | LandedCost) -> str:
    url = item.product_url
    if not url or not url.startswith(("http://", "https://")):
        return "未提供可用链接，请在平台内搜索商品名称"
    safe_url = (
        url.strip()
        .replace(" ", "%20")
        .replace("(", "%28")
        .replace(")", "%29")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )
    label = "打开平台搜索页" if item.link_kind == "marketplace_search" else "查看平台商品页"
    return f"[{label}]({safe_url})"


def _markdown_result_table(items: list[Candidate | LandedCost]) -> list[str]:
    lines = [
        "| 排名 | 商品 | 平台 | 估算到手价 | 预计配送 |",
        "| ---: | --- | --- | ---: | ---: |",
    ]
    if not items:
        lines.append("| - | 暂无可直接推荐的商品 | - | - | - |")
        return lines
    for rank, item in enumerate(items, 1):
        eta_days = getattr(item, "eta_days", None)
        eta = f"约 {eta_days} 天" if eta_days is not None else "待确认"
        lines.append(
            f"| {rank} | {_markdown_cell(item.title)} | {_platform_label(item.platform)} | "
            f"{_money(getattr(item, 'landed_cny', None))} | {eta} |"
        )
    return lines


def render_markdown(report: ResearchReportSnapshot) -> str:
    recommendations: list[Candidate | LandedCost] = list(
        report.recommendations or report.matching_offers
    )
    verdict_title, verdict_body = _customer_verdict(report, recommendations)
    destination = report.resolved_intent.destination if report.resolved_intent else "中国大陆"
    lines = [
        "# Shopping Agent 购物研究报告",
        "",
        f"> 数据快照：{_customer_timestamp(report.snapshot_effective_at)}  ",
        f"> 配送地区：{destination}  ",
        f"> 研究需求：{_markdown_cell(report.query)}",
        "",
        f"> **数据说明：** {_customer_data_notice(report)}",
        "",
        "## 购买建议",
        "",
        f"### {_markdown_cell(verdict_title)}",
        "",
        verdict_body,
        "",
        "## 推荐结果与到手价比较",
        "",
        *_markdown_result_table(recommendations),
        "",
    ]

    if recommendations:
        lines.extend(["## 推荐商品详情", ""])
        for rank, item in enumerate(recommendations, 1):
            lines.extend(
                [
                    f"### {rank}. {_markdown_cell(item.title)}",
                    "",
                    f"- **平台：** {_platform_label(item.platform)}",
                    f"- **估算到手价：** {_money(getattr(item, 'landed_cny', None))}",
                    f"- **预计配送：** 约 {getattr(item, 'eta_days', '待确认')} 天",
                    f"- **商品信息：** {_markdown_cell(_customer_attributes(item))}",
                    f"- **推荐理由：** {_customer_selection_reason(item, rank)}",
                    f"- **购买入口：** {_markdown_item_link(item)}",
                    "",
                    "| 费用项目 | 金额 |",
                    "| --- | ---: |",
                    f"| 平台标价 | {_markdown_cell(item.currency)} {item.price:,.2f} |",
                    f"| 折合商品价 | {_money(getattr(item, 'price_cny', None))} |",
                    f"| 预估运费 | {_money(getattr(item, 'shipping_cny', None))} |",
                    f"| 预估进口税费 | {_money(_import_tax_amount(item))} |",
                    f"| **估算到手价** | **{_money(getattr(item, 'landed_cny', None))}** |",
                    "",
                ]
            )
            tax_summary = _tax_evidence_summary(item)
            if tax_summary:
                lines.extend([f"- **税务依据：** {_markdown_cell(tax_summary)}。", ""])
            fx_summary = _fx_evidence_summary(item)
            if fx_summary:
                lines.extend([f"- **换算依据：** {_markdown_cell(fx_summary)}。", ""])
            shipping_summary = _shipping_evidence_summary(item)
            if shipping_summary:
                lines.extend([f"- **运费依据：** {_markdown_cell(shipping_summary)}。", ""])

    constraints = _customer_constraint_summary(report)
    preferences = _customer_preference_summary(report)
    ranking = " → ".join(
        RANKING_LABELS.get(item, item) for item in report.ranking_profile.priority_order
    )
    lines.extend(["## 筛选依据", ""])
    constraint_text = "；".join(_markdown_cell(item) for item in constraints)
    preference_text = "、".join(_markdown_cell(item) for item in preferences)
    lines.append(f"- **硬性条件：** {constraint_text or '本次未设置额外硬性条件'}。")
    lines.append(f"- **偏好：** {preference_text or '本次未设置额外偏好'}。")
    lines.append(f"- **综合排序：** {ranking}。")
    for override in report.task_overrides:
        label = PREFERENCE_FIELD_LABELS.get(override.field, "已保存偏好")
        lines.append(f"- **本次优先：** {label}按“{_markdown_cell(override.value)}”处理。")

    lines.extend(["", "## 工作假设", ""])
    if report.working_assumptions:
        lines.extend(
            f"- {_markdown_cell(_customer_assumption(item.field, item.value))}。"
            for item in report.working_assumptions
        )
    else:
        lines.append("- 本次没有额外工作假设。")

    lines.extend(["", "## 排除项", ""])
    if report.exclusions:
        lines.extend(
            [
                "以下商品因未满足硬性条件而没有进入推荐：",
                "",
                "| 商品 | 平台 | 主要原因 |",
                "| --- | --- | --- |",
            ]
        )
        for exclusion in report.exclusions:
            reasons = "、".join(
                evaluation.constraint.label for evaluation in exclusion.violated_constraints
            )
            lines.append(
                f"| {_markdown_cell(exclusion.title)} | {_platform_label(exclusion.platform)} | "
                f"{_markdown_cell(reasons)} |"
            )
    else:
        lines.append("本次没有商品因硬性条件被排除。")
    if report.unverified_candidates:
        names = "、".join(item.title for item in report.unverified_candidates)
        lines.extend(["", f"关键信息不足、暂不推荐：{_markdown_cell(names)}。"])
    if report.alternative_candidates:
        names = "、".join(item.title for item in report.alternative_candidates)
        lines.extend(["", f"无法确认是目标同款、未参与同款排名：{_markdown_cell(names)}。"])
    if report.calculation_exclusions:
        names = "、".join(item.title for item in report.calculation_exclusions)
        lines.extend(["", f"价格或币种无法可靠换算、未参与到手价排名：{_markdown_cell(names)}。"])
    if report.shipping_exclusions:
        names = "、".join(item.title for item in report.shipping_exclusions)
        lines.extend(["", f"缺少有效运费报价、未参与到手价排名：{_markdown_cell(names)}。"])
    if report.tax_exclusions:
        names = "、".join(item.title for item in report.tax_exclusions)
        lines.extend(
            [
                "",
                (
                    "缺少 HS Code、原产地、进口模式或有效税率证据、未参与到手价排名："
                    f"{_markdown_cell(names)}。"
                ),
            ]
        )
    if report.relaxation_suggestions:
        lines.extend(["", "如需扩大选择范围，可以考虑：", ""])
        lines.extend(
            f"- {_markdown_cell(item.suggestion)}" for item in report.relaxation_suggestions
        )

    lines.extend(
        [
            "",
            "## 数据来源与价格说明",
            "",
            "| 平台 | 状态 | 数据来源 | 本次说明 |",
            "| --- | --- | --- | --- |",
        ]
    )
    if report.providers:
        for name, metadata in sorted(report.providers.items()):
            lines.append(
                f"| {_platform_label(name)} | {_customer_provider_status(metadata)} | "
                f"{PROVIDER_SOURCE_LABELS[metadata.source]} | "
                f"{_markdown_cell(_customer_provider_note(metadata))} |"
            )
    else:
        lines.append("| - | 暂无 | 暂无 | 本次没有可用的平台数据 |")
    lines.extend(
        [
            "",
            f"- **汇率来源：** {_customer_exchange_source(report.exchange_rate.source)}。",
            f"- **汇率数据商：** {'、'.join(report.exchange_rate.providers) or '无需换算'}。",
            f"- **基准币种：** {report.exchange_rate.base_currency}。",
            f"- **最新报价时间：** {report.exchange_rate.effective_date}。",
            f"- **最终支付提示：** {report.exchange_rate.settlement_notice}",
            "",
            "## 下单前提醒",
            "",
            f"- {CUSTOMER_ESTIMATE_BOUNDARY}",
            "- 本报告是生成时的数据快照，不代表当前市场状态。",
        ]
    )
    if report.lineage is not None:
        relation = "重新研究" if report.lineage.relation == "rerun" else "放宽条件后重新研究"
        lines.append(f"- 本报告由{relation}生成，旧报告仍作为历史记录保留。")
    return "\n".join(lines).replace("\u2011", "-") + "\n"


def render_json(report: ResearchReportSnapshot) -> str:
    return (
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    )


def _paragraph(text: str, style: Any) -> Any:
    from reportlab.platypus import Paragraph

    return Paragraph(html.escape(text).replace("\n", "<br/>"), style)


def _pdf_font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFError, TTFont

    configured = os.getenv("REPORT_PDF_FONT")
    if not configured:
        configured = None
    candidates = [
        configured,
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for path in candidates:
        if not path:
            continue
        try:
            pdfmetrics.registerFont(TTFont("ShoppingReportFont", path, subfontIndex=0))
            return "ShoppingReportFont"
        except (OSError, TTFError, ValueError, TypeError):
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def _money(value: float | None) -> str:
    return "待确认" if value is None else f"¥{value:,.2f}"


def _import_tax_amount(item: Candidate | LandedCost) -> float | None:
    total = getattr(item, "import_tax_cny", None)
    return total if total is not None else getattr(item, "duty_cny", None)


def _tax_evidence_summary(item: Candidate | LandedCost) -> str | None:
    breakdown = getattr(item, "tax_breakdown", None)
    if breakdown is None:
        return None
    regime = IMPORT_REGIME_LABELS.get(breakdown.import_regime, breakdown.import_regime)
    summary = (
        f"HS Code {breakdown.hs_code}；原产地 {breakdown.country_of_origin}；"
        f"进口方式 {regime}；税率来源 {breakdown.provider}；"
        f"税率生效日 {breakdown.effective_date}"
    )
    if breakdown.tax_exemption_reason:
        summary += f"；税费减免 {breakdown.tax_exemption_reason}"
    valuation = breakdown.customs_valuation
    if valuation is not None:
        summary += f"；CIF 完税价格 {_money(valuation.customs_value_cny)}"
        conversion = valuation.customs_conversion
        if conversion is not None:
            summary += (
                f"；海关 {conversion.assessment_month} 月计税汇率 1 "
                f"{conversion.source_currency}=¥{conversion.rate_to_cny:g}"
            )
    return summary


def _fx_evidence_summary(item: Candidate | LandedCost) -> str | None:
    quote = item.price_conversion
    if quote is None:
        return "商品以人民币标价，无需换算" if item.currency.upper() == "CNY" else None
    markup = {
        "included": "报价已包含已披露的换汇加价",
        "excluded": "报价不含支付机构加价",
        "unknown": "换汇加价情况由最终支付机构决定",
    }[quote.markup_status]
    if quote.markup_bps is not None:
        markup += f" {quote.markup_bps:g}bp"
    return (
        f"1 {quote.source_currency}=¥{quote.rate_to_cny:g}；{quote.provider}；"
        f"观测于 {_customer_timestamp(quote.observed_at)}；{markup}"
    )


def _shipping_evidence_summary(item: Candidate | LandedCost) -> str | None:
    quote = item.shipping_quote
    if quote is None:
        return None
    parts = [
        f"{quote.origin_country} 至中国大陆",
        quote.service_name,
        f"{quote.provider} 报价",
    ]
    if quote.chargeable_weight_kg is not None:
        parts.append(f"计费重量 {quote.chargeable_weight_kg:g}kg")
    if quote.surcharge_amount:
        parts.append(f"附加费 {quote.currency} {quote.surcharge_amount:,.2f}")
    if quote.discount_amount:
        parts.append(f"优惠 {quote.currency} {quote.discount_amount:,.2f}")
    parts.append(f"时效 {quote.eta_min_days}-{quote.eta_max_days} 天")
    parts.append(f"观测于 {_customer_timestamp(quote.observed_at)}")
    return "；".join(parts)


def _customer_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            china_time = parsed.astimezone(timezone(timedelta(hours=8)))
            return f"{china_time:%Y-%m-%d %H:%M}（北京时间）"
    except ValueError:
        pass
    return value.replace("T", " ")[:22]


def _platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def _customer_data_notice(report: ResearchReportSnapshot) -> str:
    if report.data_mode == "sandbox":
        return "本报告使用演示数据，用于验证流程，不代表购物平台的实时价格、库存或配送承诺。"
    if report.data_mode == "mixed":
        return "本报告混合使用数据提供商通道与演示数据；每个平台的来源已在报告末页说明。"
    if report.result_kind == "partial":
        return "部分平台数据暂不可用，本报告只根据本次成功返回的数据给出建议。"
    return "商品信息来自已配置的数据提供商通道，购买前仍需在平台结算页核对。"


def _customer_verdict(
    report: ResearchReportSnapshot, items: list[Candidate | LandedCost]
) -> tuple[str, str]:
    if report.match_status == "no_match" or not items:
        return (
            "暂不建议直接购买",
            "当前没有商品同时满足全部硬性条件。建议先查看筛选原因，再决定是否放宽条件。",
        )
    lead = items[0]
    landed = getattr(lead, "landed_cny", None)
    mode = "同款报价" if report.mode == "exact_offer_comparison" else "候选商品"
    return (
        f"优先考虑：{lead.title}",
        f"在符合条件的{mode}中综合排序第一，估算到手价 {_money(landed)}。",
    )


def _customer_attributes(item: Candidate | LandedCost) -> str:
    details: list[str] = []
    for key, label in ATTRIBUTE_LABELS.items():
        value = item.attributes.get(key)
        if value is None:
            continue
        if key == "weight_kg" and isinstance(value, int | float):
            rendered = f"{value:g} kg"
        elif isinstance(value, list):
            rendered = "、".join(str(part) for part in value[:3])
        elif isinstance(value, str | int | float | bool):
            rendered = str(value)
        else:
            continue
        details.append(f"{label}：{rendered}")
    return "；".join(details[:4]) or "商品规格请在平台页面核对"


def _customer_selection_reason(item: Candidate | LandedCost, rank: int) -> str:
    reasons = ["已通过本次可验证的硬性条件"]
    if rank == 1:
        reasons.append("综合排序第一")
    if item.rating is not None:
        reasons.append(f"评分 {item.rating:g}")
    if item.sales is not None:
        reasons.append(f"销量 {item.sales:,}")
    return "，".join(reasons) + "。"


def _customer_provider_status(metadata: ProviderMetadata) -> str:
    if metadata.source == "fixture":
        return "演示模式"
    return PROVIDER_STATUS_LABELS[metadata.status]


def _customer_provider_note(metadata: ProviderMetadata) -> str:
    if metadata.source == "fixture":
        channel_status = PROVIDER_STATUS_LABELS[metadata.status]
        return f"非实时；原通道状态：{channel_status}"
    if metadata.status == "ok":
        return "本次返回正常"
    if metadata.failure_reason:
        return PROVIDER_FAILURE_LABELS.get(metadata.failure_reason, "本次数据不可用")
    return "本次返回不完整"


def _customer_exchange_source(source: str) -> str:
    if source == "offer-level-quotes":
        return "每件商品随附的数据商换算报价"
    if source == "native-CNY":
        return "商品原币为人民币，无需换算"
    if source == "unspecified":
        return "未单独标注"
    return source


def _customer_constraint_summary(report: ResearchReportSnapshot) -> list[str]:
    if report.resolved_intent is None:
        return []
    return [constraint.label for constraint in report.resolved_intent.hard_constraints]


def _customer_preference_summary(report: ResearchReportSnapshot) -> list[str]:
    if report.resolved_intent is None:
        return []
    intent = report.resolved_intent
    values = [
        *intent.material_preferences,
        *intent.style_preferences,
        *intent.soft_preferences,
    ]
    return list(dict.fromkeys(value for value in values if value))


def _customer_assumption(field: str, value: str) -> str:
    label = ASSUMPTION_FIELD_LABELS.get(field, "可选信息")
    if value in {"不设限", "未指定"}:
        return f"未指定{label}，本次不限制{label}"
    return f"{label}按“{value}”处理"


def render_pdf(report: ResearchReportSnapshot) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT, TA_RIGHT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.platypus import (
            HRFlowable,
            PageBreak,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise ReportGenerationError("reportlab is required to generate PDF reports") from exc

    font_name = _pdf_font()
    styles = getSampleStyleSheet()
    ink = colors.HexColor("#17202a")
    muted = colors.HexColor("#5f6b76")
    line = colors.HexColor("#dfe3e6")
    paper = colors.HexColor("#f7f8f8")
    accent = colors.HexColor("#bd4937")
    accent_soft = colors.HexColor("#fff0ec")
    positive = colors.HexColor("#0f766e")
    positive_soft = colors.HexColor("#e8f5f2")
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=23,
        leading=30,
        alignment=TA_LEFT,
        textColor=ink,
        spaceAfter=3,
    )
    heading = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=14.5,
        leading=20,
        textColor=ink,
        spaceBefore=7,
        spaceAfter=7,
    )
    body = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9.5,
        leading=15,
        wordWrap="CJK",
        spaceAfter=5,
        textColor=ink,
    )
    small = ParagraphStyle(
        "ReportSmall",
        parent=body,
        fontSize=8,
        leading=12,
        textColor=muted,
    )
    label = ParagraphStyle(
        "ReportLabel",
        parent=small,
        fontName=font_name,
        fontSize=8,
        leading=11,
        textColor=muted,
        spaceAfter=1,
    )
    kicker = ParagraphStyle(
        "ReportKicker",
        parent=small,
        fontName=font_name,
        fontSize=8.5,
        leading=12,
        textColor=accent,
        spaceAfter=4,
    )
    table_header = ParagraphStyle(
        "ReportTableHeader",
        parent=small,
        fontName=font_name,
        fontSize=8,
        leading=11,
        textColor=muted,
    )
    table_body = ParagraphStyle(
        "ReportTableBody",
        parent=body,
        fontName=font_name,
        fontSize=8.5,
        leading=12,
        spaceAfter=0,
    )
    table_body_right = ParagraphStyle(
        "ReportTableBodyRight",
        parent=table_body,
        alignment=TA_RIGHT,
    )
    card_title = ParagraphStyle(
        "ReportCardTitle",
        parent=body,
        fontName=font_name,
        fontSize=11,
        leading=15,
        textColor=ink,
        spaceAfter=0,
    )
    price = ParagraphStyle(
        "ReportPrice",
        parent=body,
        fontName=font_name,
        fontSize=16,
        leading=21,
        alignment=TA_RIGHT,
        textColor=accent,
        spaceAfter=0,
    )

    def section(name: str, eyebrow: str | None = None) -> None:
        if eyebrow:
            story.append(_paragraph(eyebrow, kicker))
        story.append(_paragraph(name, heading))

    def styled_table(
        rows: list[list[Any]],
        widths: list[float],
        commands: list[tuple[Any, ...]] | None = None,
        repeat_rows: int = 0,
    ) -> Any:
        table = Table(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
        base_commands: list[tuple[Any, ...]] = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]
        if commands:
            base_commands.extend(commands)
        table.setStyle(TableStyle(base_commands))
        return table

    ranked_items: list[Candidate | LandedCost] = list(
        (report.recommendations or report.matching_offers)[:3]
    )
    verdict_title, verdict_body = _customer_verdict(report, ranked_items)
    story: list[Any] = [
        _paragraph("SHOPPING AGENT · 跨境购物研究", kicker),
        _paragraph("购物研究报告", title),
        _paragraph(
            f"数据快照：{_customer_timestamp(report.snapshot_effective_at)} · "
            f"配送地区：{report.resolved_intent.destination if report.resolved_intent else '中国大陆'}",
            small,
        ),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=0.8, color=line),
        Spacer(1, 8),
    ]

    story.append(_paragraph("你的需求", label))
    query_box = styled_table(
        [[_paragraph(report.query, body)]],
        [180 * mm],
        [
            ("BACKGROUND", (0, 0), (-1, -1), paper),
            ("BOX", (0, 0), (-1, -1), 0.6, line),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ],
    )
    story.extend([query_box, Spacer(1, 10)])

    verdict = styled_table(
        [[_paragraph(verdict_title, card_title)], [_paragraph(verdict_body, body)]],
        [180 * mm],
        [
            ("BACKGROUND", (0, 0), (-1, -1), positive_soft),
            ("BOX", (0, 0), (-1, -1), 0.8, positive),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (0, 0), 8),
            ("BOTTOMPADDING", (0, 0), (0, 0), 2),
            ("TOPPADDING", (0, 1), (0, 1), 2),
            ("BOTTOMPADDING", (0, 1), (0, 1), 8),
        ],
    )
    story.extend([verdict, Spacer(1, 8)])
    source_notice = styled_table(
        [[_paragraph(_customer_data_notice(report), small)]],
        [180 * mm],
        [
            ("BACKGROUND", (0, 0), (-1, -1), accent_soft),
            ("LINEBEFORE", (0, 0), (0, -1), 3, accent),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ],
    )
    story.append(source_notice)

    section("推荐结果一览", "先看结论")
    if ranked_items:
        overview_rows: list[list[Any]] = [
            [
                _paragraph("排序", table_header),
                _paragraph("商品", table_header),
                _paragraph("平台", table_header),
                _paragraph("估算到手价", table_header),
                _paragraph("配送", table_header),
            ]
        ]
        for rank, item in enumerate(ranked_items, 1):
            overview_rows.append(
                [
                    _paragraph(str(rank), table_body),
                    _paragraph(item.title, table_body),
                    _paragraph(_platform_label(item.platform), table_body),
                    _paragraph(_money(getattr(item, "landed_cny", None)), table_body_right),
                    _paragraph(
                        f"约 {getattr(item, 'eta_days', 0)} 天"
                        if getattr(item, "eta_days", None) is not None
                        else "待确认",
                        table_body_right,
                    ),
                ]
            )
        story.append(
            styled_table(
                overview_rows,
                [12 * mm, 74 * mm, 28 * mm, 39 * mm, 27 * mm],
                [
                    ("BACKGROUND", (0, 0), (-1, 0), paper),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.8, line),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.4, line),
                    ("TOPPADDING", (0, 0), (-1, 0), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ],
                repeat_rows=1,
            )
        )
        if len(report.recommendations or report.matching_offers) > 3:
            story.append(_paragraph("为便于快速决策，客户版仅展示综合排序前三名。", small))
    else:
        story.append(
            _paragraph(
                f"本次核对了 {len(report.product_evidence)} 条商品信息，但没有可直接推荐的结果。",
                body,
            )
        )

    constraints = _customer_constraint_summary(report)
    preferences = _customer_preference_summary(report)
    section("本次如何筛选", "决策依据")
    if constraints:
        story.append(_paragraph(f"硬性条件：{'；'.join(constraints)}。", body))
    if preferences:
        story.append(_paragraph(f"偏好：{'、'.join(preferences)}。", body))
    ranking = " → ".join(
        RANKING_LABELS.get(item, item) for item in report.ranking_profile.priority_order
    )
    story.append(_paragraph(f"综合排序：{ranking}。", body))

    if ranked_items:
        story.append(PageBreak())
        section("前三名详细对比", "推荐详情")
        for rank, item in enumerate(ranked_items, 1):
            item_header = styled_table(
                [
                    [
                        _paragraph(f"第 {rank} 名 · {item.title}", card_title),
                        _paragraph(_money(getattr(item, "landed_cny", None)), price),
                    ],
                    [
                        _paragraph(
                            f"{_platform_label(item.platform)} · "
                            f"预计 {getattr(item, 'eta_days', '待确认')} 天送达",
                            small,
                        ),
                        _paragraph("估算到手价", table_body_right),
                    ],
                ],
                [123 * mm, 57 * mm],
                [
                    ("BACKGROUND", (0, 0), (-1, -1), paper),
                    ("BOX", (0, 0), (-1, -1), 0.6, line),
                    ("SPAN", (0, 0), (0, 0)),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
                    ("TOPPADDING", (0, 1), (-1, 1), 1),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
                ],
            )
            story.append(item_header)
            breakdown_rows = [
                [
                    _paragraph("平台标价", table_header),
                    _paragraph("折合商品价", table_header),
                    _paragraph("运费", table_header),
                    _paragraph("进口税费", table_header),
                    _paragraph("到手价", table_header),
                ],
                [
                    _paragraph(f"{item.currency} {item.price:,.2f}", table_body),
                    _paragraph(_money(getattr(item, "price_cny", None)), table_body),
                    _paragraph(_money(getattr(item, "shipping_cny", None)), table_body),
                    _paragraph(_money(_import_tax_amount(item)), table_body),
                    _paragraph(_money(getattr(item, "landed_cny", None)), table_body),
                ],
            ]
            story.append(
                styled_table(
                    breakdown_rows,
                    [36 * mm] * 5,
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
                        ("BOX", (0, 0), (-1, -1), 0.6, line),
                        ("INNERGRID", (0, 0), (-1, -1), 0.35, line),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ],
                )
            )
            tax_summary = _tax_evidence_summary(item)
            if tax_summary:
                story.append(_paragraph(f"税务依据：{tax_summary}。", small))
            fx_summary = _fx_evidence_summary(item)
            if fx_summary:
                story.append(_paragraph(f"换算依据：{fx_summary}。", small))
            shipping_summary = _shipping_evidence_summary(item)
            if shipping_summary:
                story.append(_paragraph(f"运费依据：{shipping_summary}。", small))
            story.append(_paragraph(_customer_attributes(item), body))
            story.append(_paragraph(_customer_selection_reason(item, rank), small))
            if item.product_url:
                link_label = (
                    "平台搜索结果（需再次确认具体商品）"
                    if item.link_kind == "marketplace_search"
                    else "平台商品详情"
                )
                story.append(_paragraph(f"购买入口：{link_label}", small))
            story.append(Spacer(1, 7))

    story.append(PageBreak())
    section("筛选结果与购买提醒", "下单前确认")
    if report.exclusions:
        story.append(
            _paragraph(
                f"另有 {len(report.exclusions)} 款因未满足硬性条件而未进入推荐。主要原因如下：",
                body,
            )
        )
        for exclusion in report.exclusions[:5]:
            reasons = "、".join(
                evaluation.constraint.label for evaluation in exclusion.violated_constraints
            )
            story.append(_paragraph(f"• {exclusion.title}：{reasons}。", body))
        if len(report.exclusions) > 5:
            story.append(_paragraph("其余排除项保留在完整数据报告中。", small))
    else:
        story.append(_paragraph("没有商品因硬性条件被排除。", body))
    if report.unverified_candidates:
        story.append(
            _paragraph(
                f"另有 {len(report.unverified_candidates)} 款因关键信息不足，未列入正式推荐。",
                body,
            )
        )
    if report.alternative_candidates:
        story.append(
            _paragraph(
                f"另有 {len(report.alternative_candidates)} 款无法确认是目标同款，未参与同款价格排名。",
                body,
            )
        )
    if report.calculation_exclusions:
        story.append(
            _paragraph(
                f"另有 {len(report.calculation_exclusions)} 款因币种或价格无法可靠换算，未参与到手价排名。",
                body,
            )
        )
    if report.shipping_exclusions:
        story.append(
            _paragraph(
                f"另有 {len(report.shipping_exclusions)} 款因缺少有效运费报价，未参与到手价排名。",
                body,
            )
        )
    if report.tax_exclusions:
        story.append(
            _paragraph(
                f"另有 {len(report.tax_exclusions)} 款因缺少 HS Code、原产地、进口模式或有效税率证据，"
                "未参与到手价排名。",
                body,
            )
        )
    if report.relaxation_suggestions:
        story.append(_paragraph("如果希望扩大选择范围，可考虑：", body))
        for suggestion in report.relaxation_suggestions[:4]:
            story.append(_paragraph(f"• {suggestion.suggestion}", body))

    section("数据来源与价格说明", "透明说明")
    if report.providers:
        provider_rows: list[list[Any]] = [
            [
                _paragraph("平台", table_header),
                _paragraph("状态", table_header),
                _paragraph("数据来源", table_header),
                _paragraph("本次说明", table_header),
            ]
        ]
        for name, metadata in sorted(report.providers.items()):
            provider_rows.append(
                [
                    _paragraph(_platform_label(name), table_body),
                    _paragraph(_customer_provider_status(metadata), table_body),
                    _paragraph(PROVIDER_SOURCE_LABELS[metadata.source], table_body),
                    _paragraph(_customer_provider_note(metadata), table_body),
                ]
            )
        story.append(
            styled_table(
                provider_rows,
                [36 * mm, 28 * mm, 46 * mm, 70 * mm],
                [
                    ("BACKGROUND", (0, 0), (-1, 0), paper),
                    ("BOX", (0, 0), (-1, -1), 0.6, line),
                    ("INNERGRID", (0, 0), (-1, -1), 0.35, line),
                ],
                repeat_rows=1,
            )
        )
        story.append(Spacer(1, 6))
    story.append(
        _paragraph(
            "汇率："
            f"{_customer_exchange_source(report.exchange_rate.source)}；"
            f"基准币种 {report.exchange_rate.base_currency}；"
            f"最新报价时间 {report.exchange_rate.effective_date}。",
            body,
        )
    )
    story.append(_paragraph(f"最终支付：{report.exchange_rate.settlement_notice}", body))
    story.append(_paragraph(CUSTOMER_ESTIMATE_BOUNDARY, body))
    if report.working_assumptions:
        assumptions = "；".join(
            _customer_assumption(item.field, item.value) for item in report.working_assumptions[:3]
        )
        story.append(_paragraph(f"研究假设：{assumptions}", small))
    if report.lineage is not None:
        relation = "重新研究" if report.lineage.relation == "rerun" else "放宽条件后重新研究"
        story.append(
            _paragraph(f"版本说明：本报告由{relation}生成，旧报告仍作为历史快照保留。", small)
        )
    story.append(
        _paragraph("完整证据、计算字段和内部追溯信息保留在随附的 JSON 与 Markdown 报告中。", small)
    )

    buffer = io.BytesIO()

    def invariant_canvas(filename: Any, **kwargs: Any) -> Canvas:
        kwargs.pop("invariant", None)
        return Canvas(filename, invariant=1, **kwargs)

    def decorate(canvas: Canvas, document: Any) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 7)
        canvas.setFillColor(muted)
        canvas.drawString(15 * mm, 10 * mm, "Shopping Agent · 购物研究报告")
        canvas.drawRightString(195 * mm, 10 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title="Shopping Agent 购物研究报告",
        author="Shopping Agent",
        subject="面向中国客户的跨境购物研究摘要",
    )
    try:
        document.build(
            story, onFirstPage=decorate, onLaterPages=decorate, canvasmaker=invariant_canvas
        )
    except Exception as exc:
        raise ReportGenerationError("failed to render PDF report") from exc
    return buffer.getvalue()


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_reports(report: ResearchReportSnapshot, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rendered = {
        "markdown": render_markdown(report).encode("utf-8"),
        "json": render_json(report).encode("utf-8"),
        "pdf": render_pdf(report),
    }
    for spec in REPORT_FILE_SPECS:
        _atomic_write(directory / spec.name, rendered[spec.format])


def generate_reports(snapshot: TaskSnapshot, directory: Path) -> ResearchReportSnapshot:
    files = report_file_links(snapshot)
    report = build_report_snapshot(snapshot, files)
    write_reports(report, directory)
    return report
