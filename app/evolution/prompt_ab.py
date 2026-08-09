"""Deterministic user-hash Prompt A/B routing with explicit rollout controls."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from threading import Lock

from app.evolution.prompt_versions import PromptVersion, PromptVersionStore, prompt_store


@dataclass(frozen=True, slots=True)
class PromptABTest:
    version: PromptVersion
    ratio: float


_lock = Lock()
_current: PromptABTest | None = None


def start_ab_test(
    version: PromptVersion, *, ratio: float = 0.1, store: PromptVersionStore = prompt_store
) -> PromptABTest:
    if not 0 < ratio <= 1:
        raise ValueError("A/B ratio must be greater than 0 and at most 1")
    testing = version.model_copy(update={"status": "testing"})
    store.save(testing)
    state = PromptABTest(version=testing, ratio=ratio)
    global _current
    with _lock:
        _current = state
    return state


def finish_ab_test(
    *, promote: bool, store: PromptVersionStore = prompt_store
) -> PromptABTest | None:
    global _current
    with _lock:
        state = _current
        _current = None
    if state is None:
        return None
    if promote:
        version = store.activate(state.version.version)
    else:
        version = state.version.model_copy(update={"status": "rolled_back"})
        store.save(version)
    return PromptABTest(version=version, ratio=state.ratio)


def _in_bucket(user_id: str, ratio: float) -> bool:
    digest = hashlib.sha256(user_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / float(2**64)
    return bucket < ratio


def prompt_for_user(
    base_content: str, user_id: str | None, *, store: PromptVersionStore = prompt_store
) -> str:
    """Return an active or eligible testing prompt, otherwise the checked-in base prompt."""

    with _lock:
        state = _current
    if state is not None and user_id and _in_bucket(user_id, state.ratio):
        return state.version.content
    try:
        return store.get_active().content
    except KeyError:
        return base_content


__all__ = ["PromptABTest", "finish_ab_test", "prompt_for_user", "start_ab_test"]
