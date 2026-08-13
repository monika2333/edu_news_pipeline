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
    assert "manual_export_items" not in query
    assert "select distinct on (bi.article_id)" in query
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


def test_fetch_duplicate_match_details_returns_item_bodies() -> None:
    cursor = FakeCursor(
        [
            {
                "item_id": "11111111-1111-1111-1111-111111111111",
                "state": "suspected",
                "similarity": 0.96,
                "title": "条目一",
                "body": "报送稿正文一",
                "source": "北京日报",
                "report_date": date(2026, 8, 8),
                "report_type": "zongbao",
            }
        ]
    )

    rows = db_postgres_submission_archive.fetch_duplicate_match_details(
        cursor,
        "article-1",
    )

    assert rows == [
        {
            "item_id": "11111111-1111-1111-1111-111111111111",
            "state": "suspected",
            "similarity": 0.96,
            "title": "条目一",
            "body": "报送稿正文一",
            "source": "北京日报",
            "report_date": date(2026, 8, 8),
            "report_type": "zongbao",
        }
    ]
    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    normalized = " ".join(query.split())
    assert "join submitted_report_items i on i.id = m.item_id" in normalized
    assert "join submitted_reports r on r.id = i.report_id" in normalized
    assert "m.state <> 'dismissed'" in normalized
    assert "order by r.report_date desc" in normalized
    assert "i.body" in query
    assert params == ("article-1",)


def test_fetch_news_for_submission_dedup_keeps_scope_and_reads_cache() -> None:
    cursor = FakeCursor()

    db_postgres_submission_archive.fetch_news_for_submission_dedup(
        cursor,
        limit=None,
    )

    query, params = cursor.calls[0]
    normalized = " ".join(query.split())
    assert "status = 'ready_for_export'" in normalized
    assert "created_at >=" in normalized
    assert "Asia/Shanghai" in normalized
    assert "dedup_embedding" in normalized
    assert "dedup_embedding_model" in normalized
    assert "dedup_source_hash" in normalized
    assert "dedup_embedded_at" in normalized
    assert params == ()
