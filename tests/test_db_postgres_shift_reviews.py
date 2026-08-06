from __future__ import annotations

from datetime import date
from typing import Any, Optional

import pytest

from src.adapters import db_postgres_shift_reviews


class ShiftReviewListCursor:
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


class BulkDiscardCursor:
    def __init__(self, result: dict[str, int]) -> None:
        self.result = result
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> dict[str, int]:
        return self.result


class AdminDiscardCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.rows = [
            {
                "id": "review-1",
                "shift_id": "shift-1",
                "article_id": "article-1",
                "decision": "selected",
                "version": 2,
                "admin_discarded_at": None,
                "admin_discarded_by_user_id": None,
            },
            {
                "id": "review-1",
                "shift_id": "shift-1",
                "article_id": "article-1",
                "decision": "selected",
                "version": 2,
                "admin_discarded_at": "2026-07-26T05:00:00+00:00",
                "admin_discarded_by_user_id": "admin-1",
            },
        ]

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> dict[str, Any]:
        return self.rows.pop(0)


class FinalizationCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.last_query = ""
        self.active_finalization = False

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)
        self.last_query = query

    def fetchone(self) -> Optional[dict[str, Any]]:
        if "FROM duty_shifts" in self.last_query:
            return {"id": "shift-1"}
        if "SELECT sr.finalized_batch_id AS batch_id" in self.last_query:
            return (
                {"batch_id": "batch-existing"}
                if self.active_finalization
                else None
            )
        if "INSERT INTO shift_review_finalization_batches" in self.last_query:
            return {
                "id": "batch-1",
                "shift_id": "shift-1",
                "report_type": "zongbao",
                "finalized_by_user_id": "editor-1",
                "finalized_at": "2026-07-27T10:30:00+08:00",
            }
        if "FROM shift_review_finalization_batches" in self.last_query:
            return {
                "id": "batch-1",
                "shift_id": "shift-1",
                "report_type": "zongbao",
                "finalized_at": "2026-07-27T10:30:00+08:00",
            }
        if "COALESCE(max(rank), 0)" in self.last_query:
            return {"max_rank": 3}
        raise AssertionError(f"Unexpected fetchone query: {self.last_query}")

    def fetchall(self) -> list[dict[str, Any]]:
        if "SELECT sr.article_id" in self.last_query:
            return [{"article_id": "article-1"}, {"article_id": "article-2"}]
        if "SELECT article_id, finalized_rank" in self.last_query:
            return [
                {"article_id": "article-1", "finalized_rank": 1},
                {"article_id": "article-2", "finalized_rank": 2},
            ]
        if "RETURNING sr.article_id" in self.last_query:
            article_ids = (
                self.params[-1][1]
                if "finalized_batch_id = NULL" in self.last_query
                else self.params[-1][2]
            )
            return [{"article_id": article_id} for article_id in article_ids]
        raise AssertionError(f"Unexpected fetchall query: {self.last_query}")


def test_bulk_discard_reuses_manual_candidate_filters_for_preview() -> None:
    cursor = BulkDiscardCursor(
        {"matched": 2, "updated": 0, "skipped_finalized": 1}
    )

    result = db_postgres_shift_reviews.bulk_discard_shift_candidates(
        cursor,
        shift_id="shift-1",
        actor_user_id="editor-1",
        region="internal",
        sentiment="negative",
        query="教育政策",
        created_before=date(2026, 7, 27),
        report_type="zongbao",
        dry_run=True,
    )

    query = cursor.queries[0]
    assert result == {"matched": 2, "updated": 0, "skipped_finalized": 1}
    assert "INSERT INTO shift_reviews" not in query
    assert "manual_reviews" not in query
    assert "mr.status = %s" in query
    assert "ns.status = 'ready_for_export'" in query
    assert "ns.is_beijing_related = %s" in query
    assert "ns.sentiment_label = %s" in query
    assert "ILIKE %s" in query
    assert "AT TIME ZONE 'Asia/Shanghai'" in query
    assert "ns.created_at" in query
    assert "publish_time_iso" not in query
    assert "to_timestamp(ns.publish_time)" not in query
    assert "COALESCE(mr.report_type, 'zongbao') = %s" not in query
    assert cursor.params[0] == (
        "shift-1",
        "pending",
        True,
        "negative",
        "%教育政策%",
        date(2026, 7, 27),
    )


def test_bulk_discard_only_upserts_unfinalized_pending_shift_reviews() -> None:
    cursor = BulkDiscardCursor(
        {"matched": 3, "updated": 1, "skipped_finalized": 1}
    )

    result = db_postgres_shift_reviews.bulk_discard_shift_candidates(
        cursor,
        shift_id="shift-1",
        actor_user_id="editor-1",
        region="external",
        sentiment="positive",
        dry_run=False,
    )

    query = cursor.queries[0]
    assert result == {"matched": 3, "updated": 1, "skipped_finalized": 1}
    assert "INSERT INTO shift_reviews" in query
    assert "ON CONFLICT (shift_id, article_id) DO UPDATE" in query
    assert "WHERE shift_reviews.decision = 'pending'" in query
    assert "shift_reviews.finalized_batch_id IS NULL" in query
    assert "WHERE finalized_batch_id IS NULL" in query
    assert "decision = 'discarded'" in query
    assert "decided_at = now()" in query
    assert "manual_reviews" not in query
    assert cursor.params[0][-3:] == ("editor-1", "editor-1", "zongbao")


def test_bulk_discard_matches_pending_wanbao_review_without_type_filter() -> None:
    cursor = BulkDiscardCursor(
        {"matched": 1, "updated": 1, "skipped_finalized": 0}
    )

    result = db_postgres_shift_reviews.bulk_discard_shift_candidates(
        cursor,
        shift_id="shift-with-pending-wanbao",
        actor_user_id="editor-1",
        region="internal",
        sentiment="positive",
        report_type="zongbao",
        dry_run=False,
    )

    query = cursor.queries[0]
    assert result == {"matched": 1, "updated": 1, "skipped_finalized": 0}
    assert "mr.status = %s" in query
    assert "COALESCE(mr.report_type, 'zongbao') = %s" not in query
    assert "WHERE shift_reviews.decision = 'pending'" in query
    assert cursor.params[0][-3:] == ("editor-1", "editor-1", "zongbao")


def test_admin_result_queries_separate_active_and_discarded_items() -> None:
    active_cursor = ShiftReviewListCursor()
    discarded_cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        active_cursor,
        shift_id="shift-1",
        decision="selected",
        report_type="zongbao",
        limit=200,
        offset=0,
        exclude_admin_discarded=True,
    )
    db_postgres_shift_reviews.fetch_shift_review_items(
        discarded_cursor,
        shift_id="shift-1",
        decision=None,
        report_type=None,
        limit=200,
        offset=0,
        admin_discarded_only=True,
    )

    assert all(
        "sr.admin_discarded_at IS NULL" in query
        for query in active_cursor.queries
    )
    assert all(
        "sr.admin_discarded_at IS NOT NULL" in query
        for query in discarded_cursor.queries
    )


def test_admin_unprocessed_query_excludes_imported_and_discarded_items() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        cursor,
        shift_id="shift-1",
        decision="selected",
        report_type="zongbao",
        limit=200,
        offset=0,
        include_admin_state=True,
        admin_unprocessed_only=True,
    )

    assert all(
        "LEFT JOIN manual_reviews mr ON mr.article_id = ns.article_id" in query
        for query in cursor.queries
    )
    assert all(
        "sr.admin_discarded_at IS NULL" in query
        for query in cursor.queries
    )
    assert all(
        "(mr.id IS NULL OR COALESCE(mr.status, 'pending') = 'pending')"
        in query
        for query in cursor.queries
    )


def test_shift_candidate_search_uses_body_without_selecting_it() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        cursor,
        shift_id="shift-1",
        decision="pending",
        report_type="zongbao",
        limit=10,
        offset=0,
        region="internal",
        sentiment="positive",
        query="教育政策",
        created_before=date(2026, 7, 27),
    )

    list_query = cursor.queries[-1]
    select_clause = list_query.split("FROM duty_shifts", maxsplit=1)[0]
    assert "coalesce(ns.content_markdown, '')" in list_query
    assert "ns.content_markdown" not in select_clause
    assert "ns.is_beijing_related = %s" in list_query
    assert "ns.sentiment_label = %s" in list_query
    assert "ILIKE %s" in list_query
    assert "AT TIME ZONE 'Asia/Shanghai'" in list_query
    assert cursor.params[-1][-6:-1] == (
        True,
        "positive",
        "%教育政策%",
        date(2026, 7, 27),
        10,
    )


def test_discarded_reviews_sort_by_latest_decision_with_updated_fallback() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        cursor,
        shift_id="shift-1",
        decision="discarded",
        report_type=None,
        limit=10,
        offset=0,
    )

    list_query = cursor.queries[-1]
    decided_index = list_query.index("sr.decided_at DESC NULLS LAST")
    updated_index = list_query.index("sr.updated_at DESC NULLS LAST")
    importance_index = list_query.index(
        "ns.external_importance_score DESC NULLS LAST"
    )
    stable_index = list_query.index("sr.id ASC NULLS LAST")
    assert decided_index < updated_index < importance_index < stable_index


def test_pending_reviews_keep_importance_score_order() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        cursor,
        shift_id="shift-1",
        decision="pending",
        report_type=None,
        limit=10,
        offset=0,
    )

    list_query = cursor.queries[-1]
    importance_index = list_query.index(
        "ns.external_importance_score DESC NULLS LAST"
    )
    rank_index = list_query.index("sr.rank ASC NULLS LAST")
    assert importance_index < rank_index
    assert "sr.decided_at DESC NULLS LAST" not in list_query
    assert "sr.updated_at DESC NULLS LAST" not in list_query


def test_set_admin_discarded_preserves_editor_decision() -> None:
    cursor = AdminDiscardCursor()

    before, after = db_postgres_shift_reviews.set_admin_discarded(
        cursor,
        shift_id="shift-1",
        article_id="article-1",
        actor_user_id="admin-1",
        discarded=True,
    )

    assert before["decision"] == "selected"
    assert after["decision"] == "selected"
    assert "SET admin_discarded_at" in cursor.queries[-1]
    assert "decision =" not in cursor.queries[-1]
    assert cursor.params[-1] == (True, True, "admin-1", "review-1")


def test_selected_queries_can_hide_finalized_items_and_sort_admin_results() -> None:
    current_cursor = ShiftReviewListCursor()
    admin_cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        current_cursor,
        shift_id="shift-1",
        decision="selected",
        report_type="zongbao",
        limit=200,
        offset=0,
        exclude_finalized=True,
    )
    db_postgres_shift_reviews.fetch_shift_review_items(
        admin_cursor,
        shift_id="shift-1",
        decision="selected",
        report_type="zongbao",
        limit=200,
        offset=0,
    )

    assert all(
        "sr.finalized_batch_id IS NULL" in query
        for query in current_cursor.queries
    )
    assert (
        "finalization_batch.finalized_at ASC NULLS LAST"
        in admin_cursor.queries[-1]
    )
    assert "sr.finalized_rank" in admin_cursor.queries[-1]


def test_shift_clusters_follow_current_representative_score_order() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_clusters(
        cursor,
        shift_id="shift-1",
        report_type="zongbao",
    )

    query = cursor.queries[-1]
    assert "external_importance_score DESC NULLS LAST" in query
    assert "array_agg(article_id ORDER BY item_rank)" in query
    assert (
        "representative_external_importance_score DESC NULLS LAST"
        in query
    )
    assert "unclustered_items AS" in query
    assert "'single-' || pending.article_id" in query
    assert "mc.created_at DESC" not in query
    assert cursor.params[-1] == (
        "shift-1",
        "zongbao",
    )


def test_shift_stats_are_aggregated_in_database_by_report_type() -> None:
    cursor = ShiftReviewListCursor()

    result = db_postgres_shift_reviews.fetch_shift_stats(
        cursor,
        "shift-1",
        report_type="zongbao",
    )

    aggregate_query = cursor.queries[0]
    assert "count(*) FILTER" in aggregate_query
    assert "sr.decision = 'selected'" in aggregate_query
    assert "sr.finalized_batch_id IS NULL" in aggregate_query
    assert "sr.decision = 'backup'" in aggregate_query
    assert "sr.decision = 'discarded'" in aggregate_query
    assert "COALESCE(sr.report_type, 'zongbao') = %s" in aggregate_query
    assert cursor.params[0] == ("shift-1", "zongbao")
    assert result["pending"] == 0


def test_finalize_batch_preserves_selected_decision_and_freezes_order() -> None:
    cursor = FinalizationCursor()

    result = db_postgres_shift_reviews.finalize_shift_review_batch(
        cursor,
        shift_id="shift-1",
        report_type="zongbao",
        actor_user_id="editor-1",
    )

    update_query = cursor.queries[-1]
    assert result["article_ids"] == ["article-1", "article-2"]
    assert result["item_count"] == 2
    assert "SET finalized_batch_id" in update_query
    assert "finalized_rank = ordered.finalized_rank" in update_query
    assert "decision =" not in update_query.split("FROM unnest", maxsplit=1)[0]


def test_finalize_batch_rejects_second_active_finalization() -> None:
    cursor = FinalizationCursor()
    cursor.active_finalization = True

    with pytest.raises(ValueError, match="已经定稿"):
        db_postgres_shift_reviews.finalize_shift_review_batch(
            cursor,
            shift_id="shift-1",
            report_type="zongbao",
            actor_user_id="editor-1",
        )

    assert not any(
        "INSERT INTO shift_review_finalization_batches" in query
        for query in cursor.queries
    )


def test_finalization_status_returns_metadata_without_article_list() -> None:
    cursor = FinalizationCursor()
    cursor.fetchone = lambda: {
        "batch_id": "batch-1",
        "report_type": "zongbao",
        "finalized_at": "2026-07-27T10:30:00+08:00",
        "finalized_by_display_name": "值班编辑",
        "item_count": 2,
    }

    result = db_postgres_shift_reviews.fetch_shift_finalization_status(
        cursor,
        shift_id="shift-1",
        report_type="zongbao",
    )

    query = cursor.queries[-1]
    assert result and result["batch_id"] == "batch-1"
    assert "count(sr.id) AS item_count" in query
    assert "news_summaries" not in query
    assert cursor.params[-1] == ("shift-1", "zongbao")


def test_restore_batch_clears_only_finalization_and_appends_current_rank() -> None:
    cursor = FinalizationCursor()

    result = db_postgres_shift_reviews.restore_shift_review_finalization(
        cursor,
        shift_id="shift-1",
        batch_id="batch-1",
        actor_user_id="editor-1",
    )

    update_query = cursor.queries[-1]
    assert result["restored"] == 2
    assert "finalized_batch_id = NULL" in update_query
    assert "finalized_rank = NULL" in update_query
    assert "decision =" not in update_query
    assert cursor.params[-1][2] == [4, 5]


def test_finalized_review_must_be_restored_before_direct_edit() -> None:
    cursor = AdminDiscardCursor()
    cursor.rows = [
        {
            "id": "review-1",
            "shift_id": "shift-1",
            "article_id": "article-1",
            "decision": "selected",
            "report_type": "zongbao",
            "version": 2,
            "finalized_batch_id": "batch-1",
            "finalized_rank": 1,
        }
    ]

    with pytest.raises(ValueError, match="先撤回"):
        db_postgres_shift_reviews.upsert_shift_review(
            cursor,
            shift_id="shift-1",
            article_id="article-1",
            actor_user_id="editor-1",
            expected_version=2,
            patch={"edited_summary": "不应直接修改"},
        )
