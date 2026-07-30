from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_thread_id_var: ContextVar[str | None] = ContextVar("thread_id", default=None)
_session_dir_var: ContextVar[Path | None] = ContextVar("session_dir", default=None)
_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)


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


@contextmanager
def thread_scope(thread_id: str, directory: Path, run_id: str | None = None) -> Iterator[None]:
    thread_token = _thread_id_var.set(thread_id)
    directory_token = _session_dir_var.set(directory)
    run_token = _run_id_var.set(run_id) if run_id is not None else None
    try:
        yield
    finally:
        if run_token is not None:
            _run_id_var.reset(run_token)
        _session_dir_var.reset(directory_token)
        _thread_id_var.reset(thread_token)
