from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from src.adapters import db_postgres_manual_reviews


class FakeCursor:
    def __init__(self) -> None:
        self.rowcount = 1
        self.query: Optional[str] = None
        self.params: Optional[tuple[Any, ...]] = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params


def test_discard_manual_candidates_before_date_places_filter_params_first() -> None:
    cur = FakeCursor()
    decided_at = datetime(2025, 1, 3, 8, 0, tzinfo=timezone.utc)

    updated = db_postgres_manual_reviews.discard_manual_candidates_before_date(
        cur,
        region="internal",
        sentiment="positive",
        query="keyword",
        published_before=date(2025, 1, 2),
        actor="tester",
        decided_at=decided_at,
        report_type="zongbao",
    )

    assert updated == 1
    assert cur.query is not None
    assert "decided_by = %s" in cur.query
    assert "WHERE mr.status = %s" in cur.query
    assert cur.params is not None
    assert cur.params[:6] == ("pending", "zongbao", True, "positive", "%keyword%", date(2025, 1, 2))
    assert cur.params[6] == "tester"
    assert cur.params[7] == decided_at
