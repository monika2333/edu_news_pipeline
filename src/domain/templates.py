"""Placeholders for text templates used when exporting reports."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

_BASE_DIR: Final[Path] = Path(__file__).resolve().parent


@dataclass
class BriefTemplate:
    name: str
    path: Path

    def load(self) -> str:
        return self.path.read_text(encoding="utf-8")


DEFAULT_BRIEF_TEMPLATE = BriefTemplate(
    name="default_brief",
    path=_BASE_DIR / "templates" / "brief_template.txt",
)


__all__ = ["BriefTemplate", "DEFAULT_BRIEF_TEMPLATE"]
