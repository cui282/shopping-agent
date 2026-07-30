from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _configured_root(variable: str, default: str) -> Path:
    raw = Path(os.getenv(variable, default))
    return (raw if raw.is_absolute() else PROJECT_ROOT / raw).resolve()


def output_root() -> Path:
    root = _configured_root("OUTPUT_ROOT", "output")
    root.mkdir(parents=True, exist_ok=True)
    return root


def upload_root() -> Path:
    root = _configured_root("UPLOAD_ROOT", "uploaded")
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_join(root: Path, *parts: str) -> Path:
    root = root.resolve()
    target = root.joinpath(*parts).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes configured root") from exc
    return target


def session_dir(thread_id: str) -> Path:
    path = safe_join(output_root(), thread_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
