"""Three-level Agent Skill loader: metadata, body, then optional resources."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SKILLS_DIR = Path(os.getenv("SHOPPING_SKILLS_DIR", Path(__file__).resolve().parent))


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    path: Path


@dataclass(frozen=True, slots=True)
class SkillDocument:
    metadata: SkillMetadata
    body: str
    resources: dict[str, str]


def _parse(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError(f"invalid skill frontmatter: {path}")
    values = yaml.safe_load(parts[1]) or {}
    if not isinstance(values, dict):
        raise TypeError(f"skill frontmatter must be a mapping: {path}")
    return values, parts[2].lstrip("\n")


def _metadata(path: Path) -> SkillMetadata:
    values, _ = _parse(path)
    name = str(values.get("name") or path.parent.name).strip()
    description = str(values.get("description") or "").strip()
    if not name or not description:
        raise ValueError(f"skill requires name and description: {path}")
    return SkillMetadata(name=name, description=description, path=path)


def load_skill_metadata() -> list[SkillMetadata]:
    """L1: scan only frontmatter and never load skill bodies or resources."""

    if not SKILLS_DIR.exists():
        return []
    values: list[SkillMetadata] = []
    for skill_dir in sorted(path for path in SKILLS_DIR.iterdir() if path.is_dir()):
        path = skill_dir / "SKILL.md"
        if not path.exists():
            continue
        try:
            values.append(_metadata(path))
        except (OSError, TypeError, ValueError, yaml.YAMLError):
            continue
    return values


def _find(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
        raise ValueError("invalid skill name")
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        raise KeyError(name)
    return path


def load_skill_body(name: str) -> str:
    """L2: load the selected skill's method body, excluding its frontmatter."""

    _, body = _parse(_find(name))
    return body


def _resources(path: Path) -> dict[str, str]:
    root = path.parent / "resources"
    if not root.is_dir():
        return {}
    values: dict[str, str] = {}
    for resource in sorted(root.rglob("*")):
        if resource.is_file() and resource.stat().st_size <= 256_000:
            values[str(resource.relative_to(root))] = resource.read_text(encoding="utf-8")
    return values


def load_skill(name: str, *, include_resources: bool = False) -> SkillDocument:
    """L2/L3: load a skill body and optionally its bounded resource files."""

    path = _find(name)
    metadata = _metadata(path)
    _, body = _parse(path)
    return SkillDocument(metadata, body, _resources(path) if include_resources else {})


def match_skills(query: str, *, limit: int = 3) -> list[SkillMetadata]:
    """Match likely skills using cheap description tokens before loading any body."""

    if limit < 1:
        raise ValueError("limit must be positive")
    tokens = set(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", query.casefold()))
    ranked = sorted(
        load_skill_metadata(),
        key=lambda item: (
            -len(
                tokens
                & set(
                    re.findall(
                        r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", (item.name + item.description).casefold()
                    )
                )
            ),
            item.name,
        ),
    )
    return ranked[:limit]


__all__ = [
    "SKILLS_DIR",
    "SkillDocument",
    "SkillMetadata",
    "load_skill",
    "load_skill_body",
    "load_skill_metadata",
    "match_skills",
]
