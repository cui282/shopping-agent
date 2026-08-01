from __future__ import annotations

import re

from app.schemas import HardConstraint, ShoppingPlan, WorkingAssumption
from app.tools.query_parser import extract_budget_cny, extract_product_subject

_NEGATED_VALUE = re.compile(r"(?:不要|不含|避免|排除|不考虑)\s*([^，。；,;！？!?]{1,16})")
_MATERIAL_REQUIREMENTS = (
    re.compile(
        r"(?:材质|材料)\s*(?:为|是|需要|要|采用|使用)\s*"
        r"(?P<value>[^，。；,;！？!?]{1,16})"
    ),
    re.compile(
        r"(?:需要|要|采用|使用)\s*(?P<value>[^，。；,;！？!?]{1,16})\s*"
        r"(?:材质|材料)"
    ),
)
_MEASURE = re.compile(
    r"(?P<field>重量|净重|存储|内存|容量|屏幕|显示屏|续航|电池续航)\s*"
    r"(?P<operator>不超过|不大于|小于等于|至多|少于|低于|至少|不低于|大于等于|高于|大于|等于|为|是|<=|>=|<|>)?\s*"
    r"(?P<number>\d+(?:\.\d+)?)\s*(?P<unit>kg|公斤|克|g|TB|T|GB|G|L|毫升|ml|厘米|cm|英寸|寸|小时|h)",
    re.IGNORECASE,
)
_SPECIFICATION = re.compile(
    r"(?:规格|尺寸|型号规格)\s*(?:为|是|需要|要|要求)\s*([A-Za-z0-9][A-Za-z0-9 .+/_-]{0,20})",
    re.IGNORECASE,
)
_COLOR_TERMS = (
    "黑色",
    "白色",
    "红色",
    "蓝色",
    "绿色",
    "灰色",
    "银色",
    "金色",
    "粉色",
    "棕色",
    "米色",
    "彩色",
)
_STYLE_TERMS = ("简约", "复古", "运动", "商务", "通勤", "专业", "休闲")


def _constraint_id(field: str, operator: str, index: int) -> str:
    return f"{field}_{operator}_{index}"


def _clean_negated_value(value: str) -> str:
    value = re.sub(r"^[\s：:、，,。；;！？!?]+|[\s：:、，,。；;！？!?]+$", "", value)
    value = re.split(r"的(?=[\u4e00-\u9fffA-Za-z])", value, maxsplit=1)[0]
    value = re.sub(r"(?:材质|材料)$", "", value).strip()
    return value.rstrip("的").strip()


def _negated_field(value: str) -> tuple[str, str]:
    if any(term in value for term in _COLOR_TERMS):
        return "attribute", "color"
    if any(term in value for term in _STYLE_TERMS):
        return "attribute", "style"
    return "material", "material"


def _operator(raw: str | None) -> str:
    return {
        "不超过": "lte",
        "不大于": "lte",
        "小于等于": "lte",
        "至多": "lte",
        "少于": "lte",
        "低于": "lte",
        "至少": "gte",
        "不低于": "gte",
        "大于等于": "gte",
        "高于": "gte",
        "大于": "gte",
        "等于": "equals",
        "为": "equals",
        "是": "equals",
        "<=": "lte",
        ">=": "gte",
        "<": "lte",
        ">": "gte",
    }.get(raw or "", "equals")


def _normalize_measure(field: str, number: float, unit: str) -> tuple[str, float, str]:
    normalized_field = {
        "重量": "weight_kg",
        "净重": "weight_kg",
        "存储": "storage_gb",
        "内存": "storage_gb",
        "容量": "capacity",
        "屏幕": "display_inch",
        "显示屏": "display_inch",
        "续航": "battery_hours",
        "电池续航": "battery_hours",
    }[field]
    normalized_unit = unit.lower()
    if normalized_field == "weight_kg":
        if normalized_unit in {"g", "克"}:
            number /= 1000
        return normalized_field, number, "kg"
    if normalized_field == "storage_gb":
        if normalized_unit in {"tb", "t"}:
            number *= 1024
        return normalized_field, number, "GB"
    if normalized_field == "display_inch":
        return normalized_field, number, "inch"
    if normalized_field == "battery_hours":
        return normalized_field, number, "hours"
    return normalized_field, number, unit


def _assumptions(query: str, style_preferences: list[str]) -> list[WorkingAssumption]:
    has_color = any(term in query for term in _COLOR_TERMS)
    has_style = bool(style_preferences) or any(term in query for term in _STYLE_TERMS)
    assumptions: list[WorkingAssumption] = []
    if not has_color:
        assumptions.append(
            WorkingAssumption(
                code="optional_color_unspecified",
                field="color",
                value="不设限",
                reason="请求未指定颜色，保留 Product Evidence 中可验证的各色候选。",
            )
        )
    if not has_style:
        assumptions.append(
            WorkingAssumption(
                code="optional_style_unspecified",
                field="style",
                value="不设限",
                reason="请求未指定风格，不把缺省风格升级为 Blocking Ambiguity。",
            )
        )
    return assumptions


async def planner(query: str) -> ShoppingPlan:
    """Turn free-form Chinese shopping intent into explicit constraints."""

    budget = extract_budget_cny(query)
    category = extract_product_subject(query)

    hard_constraints: list[HardConstraint] = []
    material_preferences: list[str] = []
    for index, match in enumerate(_NEGATED_VALUE.finditer(query)):
        value = _clean_negated_value(match.group(1))
        if not value:
            continue
        kind, field = _negated_field(value)
        label_prefix = "材质" if field == "material" else "颜色" if field == "color" else "风格"
        hard_constraints.append(
            HardConstraint(
                id=_constraint_id(field, "not_contains", index),
                kind=kind,
                field=field,
                operator="not_contains",
                value=value,
                label=f"{label_prefix}不含{value}",
            )
        )
        if field == "material":
            material_preferences.append(f"不含{value}")
    if budget is not None:
        hard_constraints.insert(
            0,
            HardConstraint(
                id=_constraint_id("budget_cny", "lte", 0),
                kind="budget",
                field="budget_cny",
                operator="lte",
                value=budget,
                unit="CNY",
                label=f"到手价不超过{budget:.0f}元",
            ),
        )

    material_offset = len(hard_constraints)
    for index, pattern in enumerate(_MATERIAL_REQUIREMENTS, start=material_offset):
        for match in pattern.finditer(query):
            value = _clean_negated_value(match.group("value"))
            if not value:
                continue
            hard_constraints.append(
                HardConstraint(
                    id=_constraint_id("material", "contains", index),
                    kind="material",
                    field="material",
                    operator="contains",
                    value=value,
                    label=f"材质包含{value}",
                )
            )

    soft_map = ("轻便", "降噪", "小众", "耐用", "便携", "无线", "舒适", "通勤")
    soft_preferences = [term for term in soft_map if term in query]
    style_preferences = [term for term in ("简约", "复古", "运动", "商务") if term in query]

    measure_offset = len(hard_constraints)
    for index, match in enumerate(_MEASURE.finditer(query), start=measure_offset):
        field, number, unit = _normalize_measure(
            match.group("field"), float(match.group("number")), match.group("unit")
        )
        operator = _operator(match.group("operator"))
        hard_constraints.append(
            HardConstraint(
                id=_constraint_id(field, operator, index),
                kind="specification",
                field=field,
                operator=operator,
                value=number,
                unit=unit,
                label=f"{field}{operator}{number:g}{unit}",
            )
        )

    specification_offset = len(hard_constraints)
    for index, match in enumerate(_SPECIFICATION.finditer(query), start=specification_offset):
        value = match.group(1).strip()
        hard_constraints.append(
            HardConstraint(
                id=_constraint_id("specification", "contains", index),
                kind="specification",
                field="specification",
                operator="contains",
                value=value,
                label=f"规格包含{value}",
            )
        )

    destination = "中国大陆"
    destination_match = re.search(r"(?:寄到|送到|配送到)([^，。；,;]{2,12})", query)
    if destination_match:
        destination = destination_match.group(1).strip()

    return ShoppingPlan(
        budget_cny=budget,
        category=category,
        material_preferences=material_preferences,
        style_preferences=style_preferences,
        hard_constraints=hard_constraints,
        soft_preferences=soft_preferences,
        destination=destination,
        working_assumptions=_assumptions(query, style_preferences),
    )
