from __future__ import annotations

import re
from collections.abc import Iterable

from app.schemas import MemoryCommand, PreferenceField, RememberedPreference

_COMMAND_SPAN = re.compile(
    r"(?:以后|今后|未来|下次|之后)(?:[^，,；;。]{0,8})"
    r"(?:记住|保存|偏好|喜欢|默认|不要|不含|避免)[^，,；;。]{1,24}"
    r"|(?:不再记住|别再记住|取消记住|删除偏好|忘记)[^，,；;。]{0,24}"
)
_NEGATIVE = re.compile(r"^(?:不要|不含|避免|排除|不考虑)\s*(?P<value>.+)$")
_KNOWN_STYLES = {"简约", "复古", "运动", "商务", "通勤", "专业", "休闲"}
_FIELD_SUFFIXES = (
    ("材质", "material_preferences"),
    ("材料", "material_preferences"),
    ("风格", "style_preferences"),
)


def _split_values(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?:和|以及|、)", value) if part.strip()]


def _normalize_body(body: str) -> tuple[PreferenceField, list[str]] | None:
    body = re.sub(r"^[\s：:的]+|[\s：:的]+$", "", body)
    negative = _NEGATIVE.match(body)
    if negative is not None:
        field: PreferenceField = "avoid"
        value = negative.group("value")
    else:
        value = re.sub(r"^(?:我)?(?:喜欢|偏好|想要|要|选择|默认|是|为)\s*", "", body)
        field = "soft_preferences"
        for suffix, candidate in _FIELD_SUFFIXES:
            if value.endswith(suffix):
                value = value[: -len(suffix)]
                field = candidate  # type: ignore[assignment]
                break
        if field == "soft_preferences" and any(style in value for style in _KNOWN_STYLES):
            field = "style_preferences"

    values = _split_values(value.rstrip("的").strip())
    if not values:
        return None
    return field, values


def _command_from_match(match: re.Match[str]) -> list[MemoryCommand]:
    text = match.group(0)
    forget_match = re.match(r"(?:不再记住|别再记住|取消记住|删除偏好|忘记)", text)
    if forget_match is not None:
        body = text[forget_match.end() :]
        action = "forget"
    else:
        verb_match = re.search(r"(?:记住|保存|偏好|喜欢|默认|不要|不含|避免)", text)
        if verb_match is None:
            return []
        body = text[verb_match.end() :]
        action = "remember"

    normalized = _normalize_body(body)
    if normalized is None:
        return []
    field, values = normalized
    return [MemoryCommand(action=action, field=field, values=values)]


def parse_memory_commands(query: str) -> list[MemoryCommand]:
    """Parse only explicit future-scope remember/forget language from a shopper query."""

    commands: list[MemoryCommand] = []
    for match in _COMMAND_SPAN.finditer(query):
        commands.extend(_command_from_match(match))
    return commands


def strip_memory_commands(query: str) -> str:
    """Remove future-memory instructions before current-task planning and marketplace search."""

    matches = list(_COMMAND_SPAN.finditer(query))
    if not matches:
        return query
    cleaned = query
    for match in reversed(matches):
        cleaned = cleaned[: match.start()] + cleaned[match.end() :]
    return cleaned.strip(" \t\r\n，,；;")


def _record(preferences: dict[str, object]) -> RememberedPreference:
    fields = RememberedPreference.model_fields
    return RememberedPreference.model_validate(
        {field: preferences.get(field, []) for field in fields}
    )


def _compact(preferences: RememberedPreference) -> dict[str, list[str]]:
    return {
        field: values for field, values in preferences.model_dump(mode="json").items() if values
    }


def _update_record(
    preferences: RememberedPreference, command: MemoryCommand
) -> RememberedPreference:
    values = list(getattr(preferences, command.field))
    if command.action == "remember":
        values = list(dict.fromkeys([*values, *command.values]))
    else:
        values = [value for value in values if value not in set(command.values)]
    return preferences.model_copy(update={command.field: values})


def remembered_for_task(
    preferences: RememberedPreference, commands: Iterable[MemoryCommand]
) -> RememberedPreference:
    """Apply explicit forgetting immediately while keeping new future defaults out of this task."""

    current = preferences
    for command in commands:
        if command.action == "forget":
            current = _update_record(current, command)
    return current


async def execute_memory_commands(
    store,
    user_id: str,
    commands: Iterable[MemoryCommand],
) -> RememberedPreference:
    """Apply an already parsed command list; ordinary query text never reaches this boundary."""

    current = _record(await store.get(user_id))
    for command in commands:
        current = _update_record(current, command)
    compact = _compact(current)
    if compact:
        await store.put(user_id, compact)
    else:
        await store.delete(user_id)
    return current
