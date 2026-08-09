from __future__ import annotations

from pathlib import Path

import pytest

from app.skills import loader


def test_skill_loader_uses_metadata_then_body_then_resources(tmp_path: Path, monkeypatch) -> None:
    skill = tmp_path / "price-guide"
    (skill / "resources").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: price-guide\ndescription: 跨境比价和到手价方法\n---\n正文方法\n",
        encoding="utf-8",
    )
    (skill / "resources" / "rates.txt").write_text("rates", encoding="utf-8")
    monkeypatch.setattr(loader, "SKILLS_DIR", tmp_path)

    metadata = loader.load_skill_metadata()
    assert metadata[0].name == "price-guide"
    assert "正文方法" in loader.load_skill_body("price-guide")
    document = loader.load_skill("price-guide", include_resources=True)
    assert document.resources == {"rates.txt": "rates"}
    assert loader.match_skills("请做跨境比价")[0].name == "price-guide"


def test_skill_loader_rejects_path_traversal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(loader, "SKILLS_DIR", tmp_path)
    with pytest.raises(ValueError):
        loader.load_skill_body("../secret")
