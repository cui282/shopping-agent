"""Composable Harness controls for the deterministic Shopping Agent loop."""

from app.harness.middleware import (
    HOOK_POINTS,
    HarnessMiddleware,
    HookPoint,
    HookRejectSignal,
    harness,
    harness_hook,
)
from app.harness.phase import Phase, PhaseStateMachine, phase_machine

__all__ = [
    "HOOK_POINTS",
    "HarnessMiddleware",
    "HookPoint",
    "HookRejectSignal",
    "Phase",
    "PhaseStateMachine",
    "harness",
    "harness_hook",
    "phase_machine",
]
