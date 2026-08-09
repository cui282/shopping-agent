"""Default Harness hooks wired to the Shopping Agent's existing controls."""

from __future__ import annotations

from typing import Any

from app.harness.drift_detector import detect_drift
from app.harness.middleware import HookRejectSignal, harness
from app.harness.phase import phase_machine
from app.harness.step_validation import (
    check_schema,
    check_semantic_alignment,
    check_sequencing,
)


async def phase_permission(context: dict[str, Any]) -> dict[str, Any] | None:
    tool_name = str(context.get("tool_name", ""))
    if phase_machine.is_tool_allowed(tool_name):
        return None
    allowed = ", ".join(sorted(phase_machine.get_allowed_tools()))
    raise HookRejectSignal(
        f"工具 {tool_name} 在当前阶段 {phase_machine.get_current_phase().value} 不可用；"
        f"当前可用工具：{allowed}"
    )


async def drift_hook(context: dict[str, Any]) -> dict[str, Any] | None:
    return detect_drift(context)


def install_default_hooks() -> None:
    """Install built-in controls once per process."""

    if "phase_permission" not in harness.registered("pre_tool_call"):
        harness.register("pre_tool_call", "phase_permission", phase_permission, priority=20)
    if "sequencing_assertion" not in harness.registered("pre_tool_call"):
        harness.register("pre_tool_call", "sequencing_assertion", check_sequencing, priority=25)
    if "schema_assertion" not in harness.registered("post_tool_call"):
        harness.register("post_tool_call", "schema_assertion", check_schema, priority=40)
    if "semantic_assertion" not in harness.registered("post_tool_call"):
        harness.register(
            "post_tool_call", "semantic_assertion", check_semantic_alignment, priority=45
        )
    if "drift_detector" not in harness.registered("post_reflect"):
        harness.register("post_reflect", "drift_detector", drift_hook, priority=20)


__all__ = ["drift_hook", "install_default_hooks", "phase_permission"]
