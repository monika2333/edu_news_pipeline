from __future__ import annotations

from types import SimpleNamespace
from typing import Optional

import pytest

from src.workers import export_brief


class FakeExportNamespace:
    def __init__(self) -> None:
        self.min_scores: list[float] = []

    def fetch_candidates(self, min_score: float) -> list[object]:
        self.min_scores.append(min_score)
        return []


class FakeAdapter:
    def __init__(self) -> None:
        self.export = FakeExportNamespace()


@pytest.mark.parametrize(
    ("requested_min_score", "expected_min_score"),
    [(None, 30), (45, 45)],
)
def test_run_uses_configured_threshold_unless_explicitly_overridden(
    monkeypatch: pytest.MonkeyPatch,
    requested_min_score: Optional[int],
    expected_min_score: int,
) -> None:
    adapter = FakeAdapter()
    monkeypatch.setattr(export_brief, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        export_brief,
        "get_settings",
        lambda: SimpleNamespace(score_promotion_threshold=30),
    )

    export_brief.run(min_score=requested_min_score)

    assert adapter.export.min_scores == [expected_min_score]
