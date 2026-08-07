from __future__ import annotations

import html
import io
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.schemas import (
    Candidate,
    FileLink,
    LandedCost,
    ProviderMetadata,
    ReportEvidence,
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
    "运费、关税与配送时效均为估算；这不是 checkout guarantee。购买前请以平台结算页为准。"
)


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
    if report.exclusions or report.calculation_exclusions:
        notices.append(
            ReportNotice(
                code="exclusion",
                message=(
                    f"Exclusion：{len(report.exclusions)} 个 Hard Constraint exclusion、"
                    f"{len(report.calculation_exclusions)} 个计算 exclusion 已单列，未进入推荐排名。"
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


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _evidence_payload(item: Candidate | LandedCost) -> ReportEvidence:
    payload = item.model_dump(mode="json")
    return ReportEvidence(
        item_id=payload["item_id"],
        platform=payload["platform"],
        marketplace=payload["marketplace"],
        offer_id=payload.get("offer_id"),
        title=payload["title"],
        original_price=payload["price"],
        original_currency=payload["currency"],
        price_cny=payload.get("price_cny"),
        shipping_cny=payload.get("shipping_cny"),
        duty_cny=payload.get("duty_cny"),
        landed_cny=payload.get("landed_cny"),
        eta_days=payload.get("eta_days"),
        rating=payload.get("rating"),
        sales=payload.get("sales"),
        image_url=payload.get("image_url"),
        attributes=payload.get("attributes") or {},
        identity=payload.get("identity") or {},
        identity_evidence=payload.get("identity_evidence"),
        variant_attributes=payload.get("variant_attributes") or {},
        availability=payload.get("availability"),
        provenance=payload.get("provenance"),
        source=payload["source"],
        retrieved_at=payload.get("retrieved_at"),
        link_kind=payload.get("link_kind"),
        product_url=payload.get("product_url"),
    )


def _result_payload(item: Candidate | LandedCost) -> dict[str, Any]:
    """Keep result-specific semantics beside the shared evidence projection."""

    payload = _evidence_payload(item).model_dump(mode="json")
    full = item.model_dump(mode="json")
    for field in (
        "note",
        "duty_tier",
        "shipping_estimate",
        "duty_estimate",
        "delivery_estimate",
        "reason",
        "rank",
        "offer_kind",
        "constraint_evaluations",
        "score_breakdown",
    ):
        if field in full:
            payload[field] = full[field]
    return payload


def _result_section_markdown(title: str, items: list[Any], empty: str) -> list[str]:
    lines = [f"### {title}", ""]
    if not items:
        lines.append(empty)
        return lines
    for index, item in enumerate(items, start=1):
        payload = _result_payload(item)
        lines.extend(
            [
                f"{index}. **{payload['title']}**（{payload['platform']} / {payload['item_id']}）",
                "",
                "```json",
                _json(payload),
                "```",
                "",
            ]
        )
    return lines


def render_markdown(report: ResearchReportSnapshot) -> str:
    lines = [
        "# Shopping Agent 购物研究报告",
        "",
        "## Research Snapshot",
        "",
        f"- Snapshot ID：`{report.snapshot_id}`",
        f"- Snapshot effective time：`{report.snapshot_effective_at}`",
        f"- Snapshot created at：`{report.snapshot_created_at}`",
        f"- Status：`{report.snapshot_status}`",
        f"- Data mode：`{report.data_mode}`",
        f"- {_lineage_message(report)}",
        "",
        "## Query and Resolved Intent",
        "",
        f"- Query：{report.query}",
        f"- Resolved query：{report.resolved_query or '未提供'}",
        f"- Mode：`{report.mode}`",
        f"- Recall mode：`{report.recall_provenance.mode if report.recall_provenance else 'deterministic_fallback'}`",
        "",
        "### Resolved Intent",
        "",
        "```json",
        _json(report.resolved_intent.model_dump(mode="json") if report.resolved_intent else None),
        "```",
        "",
        "## 工作假设 / Working Assumption",
        "",
    ]
    lines.extend(
        f"- `{item.field}`：{item.value}；{item.reason}" for item in report.working_assumptions
    )
    if not report.working_assumptions:
        lines.append("- 无；未指定的可选信息没有被隐藏推断。")
    lines.extend(
        ["", "## Applied Preference", "", "```json", _json(report.applied_preferences), "```", ""]
    )
    lines.extend(["## Task Override", ""])
    if report.task_overrides:
        lines.extend(
            f"- `{item.field}`：{item.value}；覆盖：{', '.join(item.overridden_values) or '无'}；{item.reason}"
            for item in report.task_overrides
        )
    else:
        lines.append("- 无；当前任务没有覆盖 Remembered Preference。")
    lines.extend(
        [
            "",
            "## Ranking Profile",
            "",
            f"- Priority：`{' > '.join(report.ranking_profile.priority_order)}`",
            f"- Explicit：`{'true' if report.ranking_profile.explicit else 'false'}`",
            "",
            "## Notices",
            "",
        ]
    )
    lines.extend(f"- **{notice.code}**：{notice.message}" for notice in report.notices)
    lines.extend(["", "## Result Summary", "", report.final_answer, ""])
    lines.extend(
        [
            "## Provider Coverage",
            "",
            "| Marketplace | Status | Source | Failure reason | Disclosure |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for name, metadata in sorted(report.providers.items()):
        lines.append(
            f"| {name} | {metadata.status} | {metadata.source} | "
            f"{metadata.failure_reason or '-'} | {metadata.fallback_reason or '-'} |"
        )
    if not report.providers:
        lines.append("| - | - | - | - | - |")
    lines.extend(["", "## Recall Provenance", ""])
    if report.recall_provenance is None:
        lines.append("- 未记录 Recall Provenance；按兼容路径视为 deterministic fallback。")
    else:
        lines.extend(
            [
                f"- Mode：`{report.recall_provenance.mode}`",
                f"- Participating channels：`{', '.join(report.recall_provenance.participating_channels) or 'none'}`",
                f"- Candidates：`{report.recall_provenance.selected_candidate_count}/{report.recall_provenance.input_candidate_count}`",
                "",
                "| Channel | State | Participated | Reason code | Disclosure |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for name, channel in report.recall_provenance.channels.items():
            lines.append(
                f"| {name} | {channel.state} | {str(channel.participated).lower()} | "
                f"{channel.reason_code} | {channel.reason} |"
            )
        if report.recall_provenance.fallback_reason:
            lines.extend(["", f"- Fallback reason：`{report.recall_provenance.fallback_reason}`"])
        personalization = report.recall_provenance.personalization
        if personalization is not None:
            lines.extend(
                [
                    "",
                    "### Personalization Provenance",
                    "",
                    f"- State：`{personalization.state}`",
                    f"- Input source：`{personalization.input_source}`",
                    f"- Preference fields：`{', '.join(personalization.preference_fields) or 'none'}`",
                    f"- Preference values：`{', '.join(personalization.preference_values) or 'none'}`",
                    f"- Signal：`{personalization.signal}`；participated：`{str(personalization.participated).lower()}`；matched candidates：`{personalization.matched_candidate_count}`",
                    f"- Reason：`{personalization.reason_code}`；{personalization.reason}",
                    "- Anonymous Shopper ID 仅用于关联研究与显式 Remembered Preference，不是登录账号、认证身份或数据所有权证明。",
                ]
            )
    lines.extend(
        [
            "",
            "## Product Evidence",
            "",
            "Product Evidence 只来自 Marketplace Gateway 或明确披露的 Sandbox fixture：",
            "",
        ]
    )
    for item in report.product_evidence:
        lines.extend(["```json", _json(_evidence_payload(item)), "```", ""])
    if not report.product_evidence:
        lines.append("- 无 Product Evidence。")
    lines.extend(["## Results", "", "## Matching Offer / 到手价比较", ""])
    lines.extend(
        _result_section_markdown("Recommendations", report.recommendations, "- 无 Recommendation。")
    )
    lines.extend(
        _result_section_markdown("Matching Offers", report.matching_offers, "- 无 Matching Offer。")
    )
    lines.extend(
        _result_section_markdown(
            "Alternative Candidate", report.alternative_candidates, "- 无 Alternative Candidate。"
        )
    )
    lines.extend(
        _result_section_markdown(
            "Unverified Candidate", report.unverified_candidates, "- 无 Unverified Candidate。"
        )
    )
    lines.extend(["## 排除项", ""])
    if report.exclusions:
        for exclusion in report.exclusions:
            lines.extend(["```json", _json(exclusion.model_dump(mode="json")), "```", ""])
    else:
        lines.append("- 无 Hard Constraint exclusion。")
    lines.extend(["### Calculation Exclusion", ""])
    if report.calculation_exclusions:
        lines.extend(
            f"- `{item.item_id}`：{item.reason_code}；{item.reason}"
            for item in report.calculation_exclusions
        )
    else:
        lines.append("- 无 calculation exclusion。")
    lines.extend(
        [
            "",
            "## Cost and Delivery Boundary",
            "",
            f"> {report.calculation_notice}",
            f"> {ESTIMATE_BOUNDARY}",
            "",
        ]
    )
    lines.extend(
        [
            "## Generated Files",
            "",
            "```json",
            _json([file.model_dump(mode="json") for file in report.files]),
            "```",
            "",
        ]
    )
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
    from reportlab.pdfbase.ttfonts import TTFont

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
        except (OSError, ValueError, TypeError):
            continue
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light"


def _pdf_item_text(item: Candidate | LandedCost) -> str:
    payload = _result_payload(item)
    return (
        f"{payload['title']}（{payload['platform']} / {payload['item_id']}）\n"
        f"Original price：{payload['original_currency']} {payload['original_price']}；"
        f"price CNY：{payload['price_cny']}；shipping CNY：{payload['shipping_cny']}；"
        f"duty CNY：{payload['duty_cny']}；landed CNY：{payload['landed_cny']}；"
        f"delivery：{payload['eta_days']} days (estimated)\n"
        f"Product Evidence：{_json(payload)}"
    )


def render_pdf(report: ResearchReportSnapshot) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfgen.canvas import Canvas
        from reportlab.platypus import (
            HRFlowable,
            KeepTogether,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise ReportGenerationError("reportlab is required to generate PDF reports") from exc

    font_name = _pdf_font()
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontName=font_name,
        fontSize=20,
        leading=26,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#12233f"),
        spaceAfter=10,
    )
    heading = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontName=font_name,
        fontSize=14,
        leading=19,
        textColor=colors.HexColor("#12233f"),
        spaceBefore=12,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9,
        leading=14,
        wordWrap="CJK",
        spaceAfter=5,
    )
    small = ParagraphStyle(
        "ReportSmall",
        parent=body,
        fontSize=7.5,
        leading=11,
        textColor=colors.HexColor("#425466"),
    )
    label = ParagraphStyle(
        "ReportLabel", parent=body, fontName=font_name, textColor=colors.HexColor("#52606d")
    )
    story: list[Any] = [
        _paragraph("Shopping Agent 购物研究报告", title),
        _paragraph("Immutable Research Snapshot", small),
        Spacer(1, 5),
    ]
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#d9e2ec")))

    def section(name: str) -> None:
        story.append(_paragraph(name, heading))

    section("Research Snapshot")
    snapshot_rows = [
        [_paragraph("Snapshot ID", label), _paragraph(report.snapshot_id, body)],
        [_paragraph("Effective time", label), _paragraph(report.snapshot_effective_at, body)],
        [_paragraph("Query", label), _paragraph(report.query, body)],
        [_paragraph("Resolved query", label), _paragraph(report.resolved_query or "未提供", body)],
        [_paragraph("Mode", label), _paragraph(report.mode, body)],
        [_paragraph("Data mode", label), _paragraph(report.data_mode, body)],
        [
            _paragraph("Recall mode", label),
            _paragraph(
                report.recall_provenance.mode
                if report.recall_provenance
                else "deterministic_fallback",
                body,
            ),
        ],
        [_paragraph("Lineage", label), _paragraph(_lineage_message(report), body)],
    ]
    table = Table(snapshot_rows, colWidths=[35 * mm, 145 * mm], repeatRows=0)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f4f8")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#d9e2ec")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d9e2ec")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(table)

    section("Resolved Intent and Preferences")
    story.append(
        _paragraph(
            f"Resolved Intent：{_json(report.resolved_intent.model_dump(mode='json') if report.resolved_intent else None)}",
            small,
        )
    )
    assumptions = (
        "; ".join(
            f"{item.field}={item.value}：{item.reason}" for item in report.working_assumptions
        )
        or "无"
    )
    story.append(_paragraph(f"Working Assumption：{assumptions}", body))
    story.append(
        _paragraph(
            f"Applied Preference：{_json(report.applied_preferences.model_dump(mode='json'))}",
            small,
        )
    )
    overrides = (
        "; ".join(f"{item.field}={item.value}：{item.reason}" for item in report.task_overrides)
        or "无"
    )
    story.append(_paragraph(f"Task Override：{overrides}", body))
    story.append(
        _paragraph(
            f"Ranking Profile：{' > '.join(report.ranking_profile.priority_order)}；explicit={report.ranking_profile.explicit}",
            body,
        )
    )

    section("Notices")
    for notice in report.notices:
        story.append(_paragraph(f"{notice.code}：{notice.message}", body))

    section("Result Summary")
    story.append(_paragraph(report.final_answer, body))
    section("Provider Coverage")
    for name, metadata in sorted(report.providers.items()):
        story.append(
            _paragraph(
                f"{name}：status={metadata.status}；source={metadata.source}；"
                f"failure_reason={metadata.failure_reason or '未提供'}；"
                f"fallback_reason={metadata.fallback_reason or '未提供'}",
                body,
            )
        )
    if not report.providers:
        story.append(_paragraph("无 Provider Coverage。", body))
    section("Recall Provenance")
    if report.recall_provenance is None:
        story.append(
            _paragraph("未记录 Recall Provenance；按兼容路径视为 deterministic fallback。", body)
        )
    else:
        story.append(
            _paragraph(
                f"Mode={report.recall_provenance.mode}；"
                f"participating={', '.join(report.recall_provenance.participating_channels) or 'none'}；"
                f"candidates={report.recall_provenance.selected_candidate_count}/"
                f"{report.recall_provenance.input_candidate_count}",
                body,
            )
        )
        for name, channel in report.recall_provenance.channels.items():
            story.append(
                _paragraph(
                    f"{name}：state={channel.state}；participated={channel.participated}；"
                    f"reason_code={channel.reason_code}；{channel.reason}",
                    body,
                )
            )
        personalization = report.recall_provenance.personalization
        if personalization is not None:
            story.append(
                _paragraph(
                    "Personalization Provenance："
                    f"state={personalization.state}；"
                    f"input_source={personalization.input_source}；"
                    f"fields={', '.join(personalization.preference_fields) or 'none'}；"
                    f"values={', '.join(personalization.preference_values) or 'none'}；"
                    f"signal={personalization.signal}；"
                    f"participated={personalization.participated}；"
                    f"matched_candidates={personalization.matched_candidate_count}；"
                    f"reason_code={personalization.reason_code}；{personalization.reason}。"
                    "Anonymous Shopper ID 仅用于关联研究与显式 Remembered Preference，"
                    "不是登录账号、认证身份或数据所有权证明。",
                    body,
                )
            )

    def item_section(name: str, items: list[Candidate | LandedCost], empty: str) -> None:
        section(name)
        if not items:
            story.append(_paragraph(empty, body))
            return
        for item in items:
            story.append(KeepTogether([_paragraph(_pdf_item_text(item), body), Spacer(1, 3)]))

    item_section("Recommendations", report.recommendations, "无 Recommendation。")
    item_section("Matching Offers", report.matching_offers, "无 Matching Offer。")
    item_section(
        "Alternative Candidate", report.alternative_candidates, "无 Alternative Candidate。"
    )
    item_section("Unverified Candidate", report.unverified_candidates, "无 Unverified Candidate。")

    section("Exclusion")
    if report.exclusions:
        for exclusion in report.exclusions:
            story.append(_paragraph(_json(exclusion.model_dump(mode="json")), small))
    else:
        story.append(_paragraph("无 Hard Constraint exclusion。", body))
    section("Calculation Exclusion")
    if report.calculation_exclusions:
        for exclusion in report.calculation_exclusions:
            story.append(
                _paragraph(
                    f"{exclusion.item_id}：{exclusion.reason_code}；{exclusion.reason}", body
                )
            )
    else:
        story.append(_paragraph("无 calculation exclusion。", body))
    section("Product Evidence")
    if report.product_evidence:
        for item in report.product_evidence:
            story.append(_paragraph(_json(_evidence_payload(item)), small))
    else:
        story.append(_paragraph("无 Product Evidence。", body))

    section("Cost and Delivery Boundary")
    story.append(_paragraph(report.calculation_notice, body))
    story.append(_paragraph(ESTIMATE_BOUNDARY, body))
    section("Generated Files")
    for file in report.files:
        story.append(_paragraph(f"{file.format}：{file.name}；report_id={file.file_id}", body))

    buffer = io.BytesIO()

    def invariant_canvas(filename: Any, **kwargs: Any) -> Canvas:
        kwargs.pop("invariant", None)
        return Canvas(filename, invariant=1, **kwargs)

    def decorate(canvas: Canvas, document: Any) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 7)
        canvas.setFillColor(colors.HexColor("#627d98"))
        canvas.drawString(18 * mm, 10 * mm, f"Shopping Agent · {report.snapshot_id}")
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=18 * mm,
        title="Shopping Agent Research Snapshot Report",
        author="Shopping Agent",
        subject=report.snapshot_id,
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
