from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.adapters import db_postgres_submission_archive


class FakeCursor:
    def __init__(self, rows: Optional[list[dict[str, Any]]] = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(
        self,
        query: str,
        params: tuple[Any, ...],
    ) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


def test_fetch_link_candidate_titles_only_reads_news_summaries() -> None:
    cursor = FakeCursor(
        [{"article_id": "article-1", "title": "测试标题"}]
    )

    rows = db_postgres_submission_archive.fetch_link_candidate_titles(
        cursor,
        compiled_date=date(2026, 7, 29),
        window_days=3,
    )

    assert rows == [{"article_id": "article-1", "title": "测试标题"}]
    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert "from news_summaries ns" in query
    assert "manual_export_items" not in query
    assert "brief_items" not in query
    assert params == (date(2026, 7, 29), 3, date(2026, 7, 29))


def test_fetch_link_candidate_bodies_uses_one_batch_query() -> None:
    cursor = FakeCursor(
        [
            {"article_id": "article-1", "body": "人工摘要"},
            {"article_id": "article-2", "body": "简报摘要"},
        ]
    )

    rows = db_postgres_submission_archive.fetch_link_candidate_bodies(
        cursor,
        article_ids=["article-1", "article-2"],
    )

    assert len(rows) == 2
    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert "select distinct on (mei.article_id)" in query
    assert "select distinct on (bi.article_id)" in query
    assert query.index("lm.final_summary") < query.index("lb.final_summary")
    assert query.index("lb.final_summary") < query.index("mr.summary")
    assert query.index("mr.summary") < query.index("ns.llm_summary")
    assert params == (["article-1", "article-2"],)


def test_fetch_link_candidate_bodies_skips_empty_batch() -> None:
    cursor = FakeCursor()

    rows = db_postgres_submission_archive.fetch_link_candidate_bodies(
        cursor,
        article_ids=[],
    )

    assert rows == []
    assert cursor.calls == []
