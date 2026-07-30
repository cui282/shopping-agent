from __future__ import annotations

from pathlib import Path

import pytest

from app.utils.path_utils import safe_join


def test_safe_join_accepts_descendants(tmp_path: Path) -> None:
    assert safe_join(tmp_path, "thread-a", "report.md") == (tmp_path / "thread-a" / "report.md")


def test_safe_join_rejects_traversal_and_prefix_collisions(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..", f"{tmp_path.name}-elsewhere", "secret")
