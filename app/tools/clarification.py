from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas import (
    ClarificationField,
    ClarificationReasonCode,
    ShoppingPlan,
)
from app.tools.destination import (
    SUPPORTED_DESTINATION,
    is_supported_destination,
    normalize_destination,
)


@dataclass(frozen=True, slots=True)
class BlockingAmbiguity:
    field: ClarificationField
    reason_code: ClarificationReasonCode
    question: str


class InvalidClarificationResponse(ValueError):
    def __init__(self, field: ClarificationField, message: str) -> None:
        self.field = field
        super().__init__(message)


_COMPARISON_PATTERN = re.compile(
    r"(?:比较|对比|比价)(?!轻便|便宜|方便|容易|复杂|简单|重要|安全|稳定|合理|喜欢|"
    r"舒适|耐用|实惠|划算|值得|高端|低价|省钱|适合|好|坏|高|低|快|慢|大|小|多|少|"
    r"轻|重|贵|远|近|强|弱|度)"
)
_EXPLICIT_PRODUCT_MODE_MARKERS = (
    "不同产品",
    "不同商品",
    "不同款",
    "多款",
    "各款",
)
_AMBIGUOUS_DESTINATION_MARKERS = (
    "海外",
    "国外",
    "境外",
    "其他地区",
    "其他地方",
    "那里",
    "不确定",
    "未确定",
    "不明确",
    "不清楚",
)
_IDENTITY_LABEL_PATTERN = re.compile(
    r"(?:型号|model|mpn|sku|gtin|ean|upc|isbn|版本|容量|规格|第\s*\d+\s*代)"
    r"\s*(?:是|为|:|：)?\s*[A-Za-z0-9\u4e00-\u9fff][^，。；,;！？!?]{0,40}",
    re.IGNORECASE,
)
_IDENTITY_TOKEN_PATTERNS = (
    re.compile(r"\b[A-Za-z][A-Za-z0-9-]*\d[A-Za-z0-9-]*\b"),
    re.compile(r"[\u4e00-\u9fff]{1,8}\d{1,4}[A-Za-z0-9-]*"),
)
_NON_IDENTITY_NUMBER_PATTERN = re.compile(
    r"(?:"
    r"(?:预算|价格|价位|不超过|低于|少于|至多|至少|数量|买|找|选)"
    r"\s*(?:人民币|RMB|CNY|[¥￥])?\s*\d+(?:\.\d+)?\s*(?:元|块|件|个|款|台|部|双|套|只|条|种)?"
    r"|第\s*\d+\s*(?:件|个|款|种)"
    r")",
    re.IGNORECASE,
)
_VAGUE_IDENTITY_MARKERS = ("未知", "不确定", "不明确", "不清楚", "待定", "随便", "不限", "都可以")
_DESTINATION_CLAUSE_PATTERN = re.compile(
    r"(?:寄到|送到|配送到|配送至|寄往|送往|发往|目的地(?:是|为)?|"
    r"收货地(?:址)?(?:是|为)?|配送地址(?:是|为)?|送货地址(?:是|为)?)[：:\s]*"
    r"[^，。；,;！？!?]{2,20}"
)
_VAGUE_RESPONSES = {"都可以", "随便", "不限", "不设限", "不知道", "不确定", "都行"}
_VARIANT_MODE_RESPONSES = {
    "比较不同产品",
    "比较不同商品",
    "不同产品",
    "不同商品",
    "不同款",
    "多款",
    "各款",
    "同一款",
    "同款",
    "同型号",
}


def _is_explicit_comparison(query: str) -> bool:
    return bool(_COMPARISON_PATTERN.search(query))


def _has_exact_variant(query: str) -> bool:
    identity_query = _NON_IDENTITY_NUMBER_PATTERN.sub("", query)
    label = _IDENTITY_LABEL_PATTERN.search(identity_query)
    if label is not None and not any(
        marker in label.group(0) for marker in _VAGUE_IDENTITY_MARKERS
    ):
        return True
    return any(pattern.search(identity_query) for pattern in _IDENTITY_TOKEN_PATTERNS)


def _has_ambiguous_destination(query: str, plan: ShoppingPlan) -> bool:
    destination_clauses = _DESTINATION_CLAUSE_PATTERN.findall(query)
    return any(
        marker in clause
        for clause in destination_clauses
        for marker in _AMBIGUOUS_DESTINATION_MARKERS
    ) or any(marker in plan.destination for marker in _AMBIGUOUS_DESTINATION_MARKERS)


def detect_blocking_ambiguity(
    query: str,
    plan: ShoppingPlan,
    resolved_fields: set[ClarificationField] | None = None,
) -> BlockingAmbiguity | None:
    """Detect only unsafe inferences before preference recall or external research."""

    resolved = resolved_fields or set()

    if (
        "mode" not in resolved
        and plan.mode == "product_research"
        and _is_explicit_comparison(query)
        and not any(marker in query for marker in _EXPLICIT_PRODUCT_MODE_MARKERS)
    ):
        return BlockingAmbiguity(
            field="mode",
            reason_code="mode_ambiguous",
            question="你要比较不同产品，还是同一 Product Variant 的跨平台报价？",
        )

    if (
        "product_variant" not in resolved
        and plan.mode == "exact_offer_comparison"
        and not _has_exact_variant(query)
    ):
        return BlockingAmbiguity(
            field="product_variant",
            reason_code="product_variant_ambiguous",
            question="请提供要比较的具体 Product Variant，例如型号、版本或容量。",
        )

    if "destination" not in resolved and _has_ambiguous_destination(query, plan):
        return BlockingAmbiguity(
            field="destination",
            reason_code="destination_ambiguous",
            question=f"请确认收货地，目前到手价计算只支持{SUPPORTED_DESTINATION}。",
        )

    return None


def normalize_clarification_response(
    field: ClarificationField,
    response: str,
) -> str:
    value = response.strip()
    if not value:
        raise InvalidClarificationResponse(field, "澄清回答不能为空")

    if field == "mode":
        if any(marker in value.casefold() for marker in ("exact", "product_variant")) or any(
            marker in value
            for marker in ("同一款", "同款", "同型号", "同一产品", "同一 Product Variant")
        ):
            return "exact_offer_comparison"
        if any(
            marker in value for marker in ("不同产品", "不同商品", "不同款", "多款", "各款")
        ) or any(
            marker in value.casefold() for marker in ("product_research", "different products")
        ):
            return "product_research"
        raise InvalidClarificationResponse(field, "请回答比较不同产品，或比较同一款跨平台报价")

    if value in _VAGUE_RESPONSES:
        raise InvalidClarificationResponse(field, "请提供具体的澄清信息")

    if field == "destination":
        normalized = normalize_destination(value)
        if is_supported_destination(normalized):
            return SUPPORTED_DESTINATION
        raise InvalidClarificationResponse(field, f"当前只支持配送至{SUPPORTED_DESTINATION}")

    if value in _VARIANT_MODE_RESPONSES:
        raise InvalidClarificationResponse(field, "请提供具体的型号、版本或容量")

    return value


def apply_clarification_context(query: str, answers: dict[str, str]) -> str:
    """Make persisted answers available to the deterministic planner on resume."""

    base_query = query
    context: list[str] = []
    mode = answers.get("mode", "")
    if mode == "product_research" or any(
        marker in mode for marker in ("不同产品", "不同商品", "不同款", "多款", "各款")
    ):
        context.append("比较不同产品")
    elif mode == "exact_offer_comparison" or any(
        marker in mode
        for marker in ("同一款", "同款", "同型号", "同一产品", "同一 Product Variant")
    ):
        context.append("比较同款")
    variant = answers.get("product_variant")
    if variant:
        context.append(f"型号 {variant}")
    destination = answers.get("destination")
    if destination:
        base_query = _DESTINATION_CLAUSE_PATTERN.sub("", base_query).strip(" \t\r\n，,；;")
        context.append(f"配送至 {destination}")
    return f"{base_query}，{'，'.join(context)}" if context else query
