from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_thread_id_var: ContextVar[str | None] = ContextVar("thread_id", default=None)
_session_dir_var: ContextVar[Path | None] = ContextVar("session_dir", default=None)
_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
_fork_depth_var: ContextVar[int] = ContextVar("fork_depth", default=0)


def get_thread_id() -> str:
    value = _thread_id_var.get()
    if value is None:
        raise RuntimeError("thread context is not active")
    return value


def get_session_dir() -> Path:
    value = _session_dir_var.get()
    if value is None:
        raise RuntimeError("session directory context is not active")
    return value


def get_run_id() -> str | None:
    return _run_id_var.get()


def get_fork_depth() -> int:
    return _fork_depth_var.get()


@contextmanager
def thread_scope(
    thread_id: str,
    directory: Path,
    run_id: str | None = None,
    *,
    fork_depth: int = 0,
) -> Iterator[None]:
    thread_token = _thread_id_var.set(thread_id)
    directory_token = _session_dir_var.set(directory)
    run_token = _run_id_var.set(run_id) if run_id is not None else None
    depth_token = _fork_depth_var.set(fork_depth)
    try:
        from app.agent.guard import reset_tool_guard

        reset_tool_guard()
    except ImportError:
        # Keep this low-level context helper importable during isolated tooling startup.
        pass
    try:
        yield
    finally:
        _fork_depth_var.reset(depth_token)
        if run_token is not None:
            _run_id_var.reset(run_token)
        _session_dir_var.reset(directory_token)
        _thread_id_var.reset(thread_token)
