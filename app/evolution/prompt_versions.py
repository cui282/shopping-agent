"""Git-like, file-backed prompt versions for reviewed runtime migrations."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import Field

from app.schemas import StrictModel

_VERSION = re.compile(r"^v(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)$")


class PromptVersion(StrictModel):
    version: str = Field(pattern=r"^v\d+\.\d+\.\d+$")
    content: str = Field(min_length=1)
    changelog: str = Field(min_length=1, max_length=4000)
    status: str = Field(default="draft", pattern=r"^(draft|testing|active|rolled_back)$")
    rubric_score: float | None = Field(default=None, ge=0, le=1)
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )


def _version_key(version: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"invalid prompt version: {version}")
    return tuple(int(match.group(name)) for name in ("major", "minor", "patch"))


class PromptVersionStore:
    """Persist reviewed versions under a runtime-owned directory."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.getenv("PROMPT_VERSION_DIR", "data/prompt_versions"))

    def _file(self, version: str) -> Path:
        _version_key(version)
        return self.path / f"{version}.yml"

    def save(self, version: PromptVersion) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self._file(version.version).write_text(
            yaml.safe_dump(version.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def get_version(self, version: str) -> PromptVersion:
        path = self._file(version)
        if not path.exists():
            raise KeyError(version)
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        return PromptVersion.model_validate(payload)

    def list_versions(self) -> list[PromptVersion]:
        if not self.path.exists():
            return []
        values: list[PromptVersion] = []
        for path in sorted(self.path.glob("v*.yml")):
            try:
                values.append(
                    PromptVersion.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
                )
            except (OSError, TypeError, ValueError, yaml.YAMLError):
                continue
        return sorted(values, key=lambda item: _version_key(item.version))

    def get_active(self) -> PromptVersion:
        active = [item for item in self.list_versions() if item.status == "active"]
        if not active:
            raise KeyError("active prompt version is not configured")
        return active[-1]

    def activate(self, version: str) -> PromptVersion:
        target = self.get_version(version).model_copy(update={"status": "active"})
        for item in self.list_versions():
            if item.version != version and item.status == "active":
                self.save(item.model_copy(update={"status": "rolled_back"}))
        self.save(target)
        return target

    def rollback(self, version: str) -> PromptVersion:
        return self.activate(version)


prompt_store = PromptVersionStore()


__all__ = ["PromptVersion", "PromptVersionStore", "prompt_store"]
