from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.schemas import (
    PreferenceDecision,
    PreferenceField,
    RememberedPreference,
    ShoppingPlan,
)


def _clean_avoided_value(value: str) -> str:
    value = re.sub(r"^[\s：:、，,。；;！？!?]+|[\s：:、，,。；;！？!?]+$", "", value)
    value = re.split(r"的(?=[\u4e00-\u9fffA-Za-z])", value, maxsplit=1)[0]
    return value.rstrip("的").strip()


@dataclass(frozen=True, slots=True)
class PreferenceResolution:
    effective_remembered: RememberedPreference
    decisions: list[PreferenceDecision]


def _comparison_value(value: str) -> str:
    return re.sub(r"\s+", "", value).lower().removeprefix("不含").removeprefix("不要")


def _current_preferences(plan: ShoppingPlan) -> dict[PreferenceField, list[str]]:
    current: dict[PreferenceField, list[str]] = {
        "material_preferences": list(plan.material_preferences),
        "style_preferences": list(plan.style_preferences),
        "soft_preferences": list(plan.soft_preferences),
        "avoid": [],
    }
    for constraint in plan.hard_constraints:
        if constraint.field == "material" and constraint.operator == "contains":
            current["material_preferences"].append(str(constraint.value))
        elif constraint.field == "material" and constraint.operator == "not_contains":
            current["avoid"].append(str(constraint.value))
    return {field: list(dict.fromkeys(values)) for field, values in current.items() if values}


def resolve_preferences(
    plan: ShoppingPlan,
    remembered: RememberedPreference | dict[str, Any] | None,
) -> PreferenceResolution:
    """Resolve current-task statements against remembered defaults without creating constraints."""

    remembered_model = (
        remembered
        if isinstance(remembered, RememberedPreference)
        else RememberedPreference.model_validate(
            {
                field: (remembered or {}).get(field, [])
                for field in RememberedPreference.model_fields
            }
        )
    )
    current = _current_preferences(plan)
    decisions: list[PreferenceDecision] = []
    for field, values in current.items():
        for value in values:
            decisions.append(
                PreferenceDecision(
                    field=field,
                    value=value,
                    status="applied",
                    source="current_request",
                    reason="当前请求明确表达，本任务优先采用。",
                )
            )

    effective: dict[str, list[str]] = {field: [] for field in RememberedPreference.model_fields}
    current_material = {
        _comparison_value(value)
        for value in [*current.get("material_preferences", []), *current.get("avoid", [])]
    }
    for field in RememberedPreference.model_fields:
        remembered_values = getattr(remembered_model, field)
        current_values = current.get(field, [])
        for value in remembered_values:
            comparable = _comparison_value(value)
            same_current = any(comparable == _comparison_value(item) for item in current_values)
            material_conflict = (
                field in {"material_preferences", "avoid"} and comparable in current_material
            )
            if same_current:
                status = "ignored"
                reason = "当前请求已明确表达相同偏好，记忆不重复参与本任务。"
            elif current_values or material_conflict:
                status = "overridden"
                reason = "当前请求存在冲突表达，已保存的偏好不覆盖本次研究。"
            else:
                status = "applied"
                reason = "作为本次研究的透明默认值参与偏好匹配排序。"
                effective[field].append(value)
            decisions.append(
                PreferenceDecision(
                    field=field,
                    value=value,
                    status=status,
                    source="remembered_preference",
                    reason=reason,
                )
            )

    return PreferenceResolution(
        effective_remembered=RememberedPreference.model_validate(effective),
        decisions=decisions,
    )


def merge_preferences(plan: ShoppingPlan, remembered: dict[str, Any]) -> ShoppingPlan:
    values = plan.model_dump()
    resolution = resolve_preferences(plan, remembered)
    for field in ("material_preferences", "style_preferences", "soft_preferences"):
        combined = list(
            dict.fromkeys([*values[field], *getattr(resolution.effective_remembered, field)])
        )
        values[field] = combined
    return ShoppingPlan.model_validate(values)


def extract_preferences(query: str, plan: ShoppingPlan) -> dict[str, Any]:
    preferences: dict[str, Any] = {
        "material_preferences": plan.material_preferences,
        "style_preferences": plan.style_preferences,
        "soft_preferences": plan.soft_preferences,
    }
    avoided = [
        cleaned
        for match in re.finditer(r"(?:不要|不含|避免)([^，。；,;]{1,12})", query)
        if (cleaned := _clean_avoided_value(match.group(1)))
    ]
    if avoided:
        preferences["avoid"] = avoided
    return {key: value for key, value in preferences.items() if value}


def merge_preference_records(
    remembered: dict[str, Any], extracted: dict[str, Any]
) -> dict[str, Any]:
    merged = dict(remembered)
    for key, value in extracted.items():
        if isinstance(value, list):
            previous = merged.get(key, [])
            previous_values = previous if isinstance(previous, list) else []
            merged[key] = list(dict.fromkeys([*previous_values, *value]))
        else:
            merged[key] = value
    return merged
