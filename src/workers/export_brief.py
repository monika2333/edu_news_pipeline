from __future__ import annotations

from typing import Optional


def run(date: Optional[str] = None, *, min_score: int = 60) -> None:
    """Export high scoring summaries into the daily brief template."""
    raise NotImplementedError("export_brief.run needs an implementation")


__all__ = ["run"]
