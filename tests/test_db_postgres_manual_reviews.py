from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

from src.adapters import db_postgres_manual_reviews, db_postgres_news_summaries


class FakeCursor:
    def __init__(self) -> None:
        self.rowcount = 1
        self.query: Optional[str] = None
        self.params: Optional[tuple[Any, ...]] = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params


class FakeFetchCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> dict[str, int]:
        return {"total": 0}

    def fetchall(self) -> list[dict[str, Any]]:
        return []


class FakeUpdateCursor:
    def __init__(self) -> None:
        self.rowcount = 0
        self.query: Optional[str] = None
        self.payload: list[tuple[Any, ...]] = []

    def executemany(self, query: str, payload: list[tuple[Any, ...]]) -> None:
        self.query = query
        self.payload = payload
        self.rowcount = len(payload)


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


def test_fetch_manual_reviews_orders_selected_items_by_manual_rank_first() -> None:
    cur = FakeFetchCursor()

    rows, total = db_postgres_manual_reviews.fetch_manual_reviews(
        cur,
        status="selected",
        limit=20,
        offset=0,
        report_type="zongbao",
    )

    assert rows == []
    assert total == 0
    assert len(cur.queries) == 2
    list_query = cur.queries[1]
    rank_index = list_query.index("mr.rank ASC NULLS LAST")
    score_index = list_query.index("ns.external_importance_score DESC NULLS LAST")
    assert rank_index < score_index


def test_update_summary_categories_updates_canonical_group_fields() -> None:
    cur = FakeUpdateCursor()

    updated = db_postgres_news_summaries.update_summary_categories(
        cur,
        [
            {
                "article_id": "a1",
                "is_beijing_related": True,
                "sentiment_label": "positive",
            },
            {
                "article_id": "a2",
                "is_beijing_related": False,
                "sentiment_label": "negative",
            },
        ],
    )

    assert updated == 2
    assert cur.query is not None
    assert "UPDATE news_summaries" in cur.query
    assert cur.payload == [(True, "positive", "a1"), (False, "negative", "a2")]
