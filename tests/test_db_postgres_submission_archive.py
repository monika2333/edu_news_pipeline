from __future__ import annotations

from datetime import date
from typing import Any, Optional

from src.adapters import db_postgres_submission_archive


class FakeCursor:
    def __init__(
        self,
        rows: Optional[list[dict[str, Any]]] = None,
        fetchone_rows: Optional[list[Optional[dict[str, Any]]]] = None,
    ) -> None:
        self.rows = rows or []
        self.fetchone_rows = fetchone_rows or []
        self.fetchone_index = 0
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(
        self,
        query: str,
        params: tuple[Any, ...],
    ) -> None:
        self.calls.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows

    def fetchone(self) -> Optional[dict[str, Any]]:
        if self.fetchone_index >= len(self.fetchone_rows):
            return None
        row = self.fetchone_rows[self.fetchone_index]
        self.fetchone_index += 1
        return row


def test_insert_report_persists_external_message_identity() -> None:
    cursor = FakeCursor(
        fetchone_rows=[
            {
                "id": "report-1",
                "ingest_source": "feishu",
                "source_message_id": "om_1",
            }
        ]
    )

    row = db_postgres_submission_archive.insert_report(
        cursor,
        report_type="wanbao",
        report_date=date(2026, 8, 21),
        compiled_date=date(2026, 8, 20),
        issue_no="总第1期",
        title_line="首都教育舆情",
        pasted_text="全文",
        ingest_source="feishu",
        source_message_id="om_1",
        source_sender_id="ou_owner",
        ignore_source_conflict=True,
    )

    assert row is not None
    query, params = cursor.calls[0]
    normalized = " ".join(query.split())
    assert "ingest_source" in normalized
    assert "source_message_id" in normalized
    assert "source_sender_id" in normalized
    assert "on conflict (ingest_source, source_message_id) do nothing" in normalized
    assert params[-3:] == ("feishu", "om_1", "ou_owner")


def test_fetch_report_by_source_message_returns_report_with_items() -> None:
    cursor = FakeCursor(
        rows=[{"id": "item-1", "title": "条目"}],
        fetchone_rows=[
            {"id": "report-1"},
            {"id": "report-1", "report_type": "wanbao"},
        ],
    )

    report = db_postgres_submission_archive.fetch_report_by_source_message(
        cursor,
        ingest_source="feishu",
        source_message_id="om_1",
    )

    assert report is not None
    assert report["id"] == "report-1"
    assert report["items"] == [{"id": "item-1", "title": "条目"}]
    assert cursor.calls[0][1] == ("feishu", "om_1")


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


def test_fetch_manual_link_candidates_uses_inclusive_date_boundaries() -> None:
    cursor = FakeCursor(
        rows=[
            {"article_id": "article-3", "linked_items": []},
            {"article_id": "article-2", "linked_items": []},
            {"article_id": "article-1", "linked_items": []},
        ],
        fetchone_rows=[
            {
                "id": "item-1",
                "title": "存档标题",
                "body": "存档正文",
                "report_type": "zongbao",
                "report_date": date(2026, 8, 10),
                "compiled_date": date(2026, 8, 9),
                "link_status": "unmatched",
                "article_id": None,
            }
        ],
    )

    result = db_postgres_submission_archive.fetch_manual_link_candidates(
        cursor,
        item_id="item-1",
        query=" 招生 ",
        window_days=15,
        limit=2,
        offset=4,
    )

    assert result is not None
    assert result["window_start"] == date(2026, 7, 25)
    assert result["window_end"] == date(2026, 8, 24)
    assert result["items"] == cursor.rows[:2]
    assert result["has_more"] is True
    assert len(cursor.calls) == 2
    query, params = cursor.calls[1]
    normalized = " ".join(query.split())
    assert "ns.created_at >= %s::date" in normalized
    assert "ns.created_at < %s::date + interval '1 day'" in normalized
    assert "coalesce(ns.title, '') ilike %s" in normalized
    assert "coalesce(ns.llm_summary, '') ilike %s" in normalized
    assert "content_markdown" not in query
    assert "count(" not in query.lower()
    assert "linked.link_status = 'matched'" in normalized
    assert "ns.created_at as ingested_at" in normalized
    assert params == (
        date(2026, 7, 25),
        date(2026, 8, 24),
        "% 招生 %",
        "% 招生 %",
        3,
        4,
    )


def test_manual_link_sets_matched_without_overwriting_automatic_evidence() -> None:
    updated = {
        "id": "item-1",
        "report_id": "report-1",
        "article_id": "article-manual",
        "link_status": "matched",
        "link_title_score": 0.71,
        "link_body_score": 0.62,
        "link_combined_score": 0.68,
        "best_candidate_article_id": "article-auto",
        "link_matched_at": "now",
        "link_decided_by": "user-1",
    }
    cursor = FakeCursor(
        fetchone_rows=[
            {"link_status": "unmatched", "article_exists": True},
            updated,
        ]
    )

    result = db_postgres_submission_archive.manual_link_item(
        cursor,
        item_id="item-1",
        article_id="article-manual",
        actor_user_id="user-1",
    )

    assert result == {"state": "updated", "item": updated}
    update_query, update_params = cursor.calls[1]
    set_clause = update_query.split("returning", maxsplit=1)[0]
    assert "link_status = 'matched'" in set_clause
    assert "link_status <> 'processing'" in set_clause
    assert "best_candidate_article_id" not in set_clause
    assert "link_title_score" not in set_clause
    assert "link_body_score" not in set_clause
    assert "link_combined_score" not in set_clause
    assert update_params == ("article-manual", "user-1", "item-1")


def test_manual_link_rejects_processing_item_before_update() -> None:
    cursor = FakeCursor(
        fetchone_rows=[
            {"link_status": "processing", "article_exists": True},
        ]
    )

    result = db_postgres_submission_archive.manual_link_item(
        cursor,
        item_id="item-1",
        article_id="article-1",
        actor_user_id="user-1",
    )

    assert result == {"state": "processing", "item": None}
    assert len(cursor.calls) == 1


def test_manual_unlink_returns_item_to_unmatched() -> None:
    updated = {
        "id": "item-1",
        "article_id": None,
        "link_status": "unmatched",
        "best_candidate_article_id": "article-auto",
        "link_title_score": 0.71,
        "link_body_score": 0.62,
        "link_combined_score": 0.68,
    }
    cursor = FakeCursor(
        fetchone_rows=[{"link_status": "matched"}, updated]
    )

    result = db_postgres_submission_archive.manual_unlink_item(
        cursor,
        item_id="item-1",
        actor_user_id="user-1",
    )

    assert result == {"state": "updated", "item": updated}
    update_query, update_params = cursor.calls[1]
    set_clause = update_query.split("returning", maxsplit=1)[0]
    assert "article_id = null" in set_clause
    assert "link_status = 'unmatched'" in set_clause
    assert "link_matched_at = null" in set_clause
    assert "link_status <> 'processing'" in set_clause
    assert "best_candidate_article_id" not in set_clause
    assert "link_title_score" not in set_clause
    assert update_params == ("user-1", "item-1")


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
