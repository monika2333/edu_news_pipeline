from __future__ import annotations

from datetime import date, datetime, timezone

_AWARE_BATCH_TS = datetime(2026, 10, 7, 6, 32, 5, 123456, tzinfo=timezone.utc)
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
            {},
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
        viewer_user_id="admin-1",
        exclude_admin_discarded=True,
    )
    db_postgres_shift_reviews.fetch_shift_review_items(
        discarded_cursor,
        shift_id="shift-1",
        decision=None,
        report_type=None,
        limit=200,
        offset=0,
        viewer_user_id="admin-1",
        admin_discarded_only=True,
    )

    assert all(
        "admin_discard.shift_review_id IS NULL" in query
        for query in active_cursor.queries
    )
    assert all(
        "admin_discard.shift_review_id IS NOT NULL" in query
        for query in discarded_cursor.queries
    )


def test_admin_unprocessed_query_includes_manual_discarded_items() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        cursor,
        shift_id="shift-1",
        decision="selected",
        report_type="zongbao",
        limit=200,
        offset=0,
        viewer_user_id="admin-1",
        include_admin_state=True,
        admin_unprocessed_only=True,
    )

    assert all(
        "LEFT JOIN manual_reviews mr" in query
        and "mr.owner_user_id = %s" in query
        for query in cursor.queries
    )
    assert all(
        "admin_discard.shift_review_id IS NULL" in query
        for query in cursor.queries
    )
    assert all(
        "COALESCE(mr.status, 'pending') IN ('pending', 'discarded')"
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
    assert "INSERT INTO shift_review_admin_discards" in cursor.queries[-2]
    assert "decision =" not in cursor.queries[-2]
    assert cursor.params[-2] == ("admin-1", "review-1", "admin-1")
    assert cursor.params[-1] == ("admin-1", "review-1")


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


def test_shift_clusters_apply_refine_filters_to_pending_cte() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_clusters(
        cursor,
        shift_id="shift-1",
        report_type="zongbao",
        hour_from=22,
        hour_to=6,
        duplicate_state="untagged",
        min_score=60,
        max_score=95,
    )

    query = cursor.queries[0]
    assert "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') >= %s" in query
    assert "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') <= %s" in query
    assert "NOT EXISTS (" in query
    assert "sdm.state <> 'dismissed'" in query
    assert "ns.external_importance_score >= %s" in query
    assert "ns.external_importance_score <= %s" in query
    # 细化子句参数追加在 (shift_id, report_type) 之后
    assert cursor.params[0] == ("shift-1", "zongbao", 22, 6, 60, 95)


def test_shift_review_items_apply_refine_filters_with_param_order() -> None:
    cursor = ShiftReviewListCursor()

    db_postgres_shift_reviews.fetch_shift_review_items(
        cursor,
        shift_id="shift-1",
        decision="pending",
        report_type="zongbao",
        limit=50,
        offset=0,
        hour_from=8,
        duplicate_state="untagged",
        min_score=60,
    )

    query = cursor.queries[0]
    assert "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') >= %s" in query
    assert "NOT EXISTS (" in query
    assert "ns.external_importance_score >= %s" in query
    # 细化子句参数在基础筛选之后、limit/offset 之前（join 参数由执行处前置）
    assert "hour" not in cursor.params[0]
    flat_params = [p for p in cursor.params[0] if p is not None]
    assert 8 in flat_params and 60 in flat_params


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
    assert query.count("unnest(") == 1
    assert "cluster_items AS MATERIALIZED" in query
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


def test_batch_membership_query_matches_single_article_contract() -> None:
    cursor = ShiftReviewListCursor()
    cursor.fetchall = lambda: [{"article_id": "article-1"}]

    result = db_postgres_shift_reviews.fetch_shift_article_ids(
        cursor,
        shift_id="shift-1",
        article_ids=["article-2", "article-1"],
    )

    query = cursor.queries[-1]
    assert result == ["article-1"]
    assert "ns.created_at >= s.starts_at" in query
    assert "ns.created_at < s.ends_at" in query
    assert "s.cancelled_at IS NULL" in query
    assert "ns.status = 'ready_for_export'" in query
    assert "ns.article_id = ANY(%s)" in query
    assert cursor.params[-1] == (
        "shift-1",
        ["article-2", "article-1"],
    )


def test_batch_review_lock_uses_deterministic_order_and_for_update() -> None:
    cursor = ShiftReviewListCursor()

    result = db_postgres_shift_reviews.fetch_shift_reviews_for_update(
        cursor,
        shift_id="shift-1",
        article_ids=["article-2", "article-1"],
    )

    query = cursor.queries[-1]
    assert result == []
    assert "article_id = ANY(%s)" in query
    assert "ORDER BY article_id" in query
    assert "FOR UPDATE" in query
    assert query.index("ORDER BY article_id") < query.index("FOR UPDATE")


def test_bulk_discard_appends_refine_filter_clauses() -> None:
    cursor = BulkDiscardCursor(
        {"matched": 2, "updated": 0, "skipped_finalized": 0}
    )

    db_postgres_shift_reviews.bulk_discard_shift_candidates(
        cursor,
        shift_id="shift-1",
        actor_user_id="editor-1",
        region="internal",
        sentiment="negative",
        query="教育政策",
        report_type="zongbao",
        dry_run=True,
        hour_from=8,
        hour_to=10,
        duplicate_state="untagged",
        min_score=5.5,
        max_score=30,
    )

    query = cursor.queries[0]
    assert "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') >= %s" in query
    assert "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') <= %s" in query
    assert "NOT EXISTS" in query
    assert "ns.external_importance_score >= %s" in query
    assert "ns.external_importance_score <= %s" in query
    # 细化子句参数紧跟在既有过滤参数之后
    assert cursor.params[0] == (
        "shift-1",
        "pending",
        True,
        "negative",
        "%教育政策%",
        8,
        10,
        5.5,
        30,
    )


def test_bulk_restore_dry_run_counts_without_write() -> None:
    cursor = BulkDiscardCursor({"matched": 3})

    result = db_postgres_shift_reviews.bulk_restore_shift_reviews(
        cursor,
        shift_id="shift-1",
        actor_user_id="editor-1",
        region="internal",
        sentiment="negative",
        query="教育政策",
        dry_run=True,
        min_score=5.5,
        max_score=30,
        decided_since=date(2026, 10, 1),
    )

    query = cursor.queries[0]
    assert result == {"matched": 3, "updated": 0}
    assert "UPDATE shift_reviews" not in query
    assert "finalized_batch_id" not in query
    assert "COALESCE(sr.decision, 'pending') = %s" in query
    assert "ns.is_beijing_related = %s" in query
    assert "ns.sentiment_label = %s" in query
    assert "ILIKE %s" in query
    assert "ns.external_importance_score >= %s" in query
    assert "ns.external_importance_score <= %s" in query
    assert "(sr.decided_at AT TIME ZONE 'Asia/Shanghai')::date >= %s" in query


class _RestoreReturningCursor:
    """执行分支用：同一条 CTE 语句经 fetchone 返回 {matched, updated}。"""
    def __init__(self, result: dict[str, int]) -> None:
        self.result = result
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> dict[str, int]:
        return self.result

    def fetchall(self) -> list[dict[str, Any]]:
        return [{"article_id": "a1"}, {"article_id": "a2"}, {"article_id": "a3"}]


def test_bulk_restore_resets_to_pending_without_touching_finalized() -> None:
    cursor = _RestoreReturningCursor({"matched": 3, "updated": 3})

    result = db_postgres_shift_reviews.bulk_restore_shift_reviews(
        cursor,
        shift_id="shift-1",
        actor_user_id="editor-1",
        dry_run=False,
        batch_decided_at=_AWARE_BATCH_TS,
    )

    query = cursor.queries[0]
    assert result == {"matched": 3, "updated": 3}
    assert "WITH matched_candidates AS MATERIALIZED" in query
    assert "UPDATE shift_reviews AS sr" in query
    assert "decision = 'pending'" in query
    assert "rank = NULL" in query
    assert "decided_at = NULL" in query
    assert "sr.version + 1" in query
    assert "updated_by_user_id = %s" in query
    assert "mc.finalized_batch_id IS NULL" in query
    # 目标表通过 matched_candidates 的 id 关联，CTE 内完成与班次/新闻的连接
    assert "sr.shift_id = s.id" in query
    assert "sr.article_id = ns.article_id" in query
    assert cursor.params[0][-1] == "editor-1"
    assert _AWARE_BATCH_TS in cursor.params[0]


def test_bulk_restore_matches_list_where_construction() -> None:
    """T6（值班）：同一组条件下，列表与恢复的 where 子句和参数一致。"""
    list_cursor = ShiftReviewListCursor()
    restore_cursor = BulkDiscardCursor({"matched": 3})
    filters = {
        "region": "internal",
        "sentiment": "negative",
        "query": "教育政策",
        "hour_from": 8,
        "duplicate_state": "untagged",
        "min_score": 5.5,
        "max_score": 30,
        "decided_since": date(2026, 10, 1),
        "batch_decided_at": _AWARE_BATCH_TS,
    }

    db_postgres_shift_reviews.fetch_shift_review_items(
        list_cursor,
        shift_id="shift-1",
        decision="discarded",
        report_type=None,
        limit=10,
        offset=0,
        **filters,
    )
    db_postgres_shift_reviews.bulk_restore_shift_reviews(
        restore_cursor,
        shift_id="shift-1",
        actor_user_id="editor-1",
        dry_run=True,
        **filters,
    )

    list_sql = list_cursor.queries[-1]
    restore_sql = restore_cursor.queries[0]
    expected_clauses = [
        "s.id = %s",
        "s.cancelled_at IS NULL",
        "ns.status = 'ready_for_export'",
        "COALESCE(sr.decision, 'pending') = %s",
        "ns.is_beijing_related = %s",
        "ns.sentiment_label = %s",
        "ILIKE %s",
        "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') >= %s",
        "NOT EXISTS",
        "ns.external_importance_score >= %s",
        "ns.external_importance_score <= %s",
        "(sr.decided_at AT TIME ZONE 'Asia/Shanghai')::date >= %s",
        "sr.decided_at = %s",
    ]
    for clause in expected_clauses:
        assert clause in list_sql, f"列表 SQL 缺少子句：{clause}"
        assert clause in restore_sql, f"恢复 SQL 缺少子句：{clause}"
    # 列表与恢复的筛选参数序列一致（恢复不含 limit/offset/actor）
    list_params = list(list_cursor.params[-1])
    restore_params = list(restore_cursor.params[0])
    filter_params = [
        True,
        "negative",
        "%教育政策%",
        8,
        5.5,
        30,
        date(2026, 10, 1),
        _AWARE_BATCH_TS,
    ]
    assert _contains_subsequence(list_params, filter_params)
    assert _contains_subsequence(restore_params, filter_params)


def _contains_subsequence(haystack: list, needle: list) -> bool:
    """按值比较的连续子序列判断（用于过滤参数顺序一致性断言）。"""
    for start in range(len(haystack) - len(needle) + 1):
        if all(
            haystack[start + offset] == needle[offset]
            for offset in range(len(needle))
        ):
            return True
    return False


def test_bulk_restore_shift_reviews_sql_semantics() -> None:
    """真实 SQL 语义：已定稿行不动，恢复行 decision=pending 且 decided_at 置空。"""
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row
    from uuid import uuid4

    from src.config import get_settings

    settings = get_settings()
    shift_id = uuid4()
    editor_id = "33333333-3333-3333-3333-333333333333"
    finalized_batch = uuid4()

    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=False,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            for table in ("duty_shifts", "news_summaries", "shift_reviews"):
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"(LIKE public.{table} INCLUDING DEFAULTS) ON COMMIT DROP"
                )
            cur.execute(
                """
                INSERT INTO duty_shifts (id, user_id, starts_at, ends_at)
                VALUES (%s, %s, '2026-10-06T22:00:00+08:00',
                        '2026-10-07T22:00:00+08:00')
                """,
                (shift_id, editor_id),
            )
            for article_id in ("n1", "n2", "n3"):
                cur.execute(
                    """
                    INSERT INTO news_summaries
                        (article_id, title, status, created_at)
                    VALUES (%s, %s, 'ready_for_export',
                            '2026-10-07T08:00:00+08:00')
                    """,
                    (article_id, f"标题{article_id}"),
                )
            cur.execute(
                """
                INSERT INTO shift_reviews
                    (shift_id, article_id, created_by_user_id,
                     updated_by_user_id, report_type, decision, rank,
                     version, decided_at, finalized_batch_id, finalized_rank)
                VALUES
                    (%s, 'n1', %s, %s, 'wanbao', 'discarded', 1, 2,
                     '2026-10-07T09:30:00+08:00', NULL, NULL),
                    (%s, 'n2', %s, %s, 'wanbao', 'discarded', 2, 1,
                     '2026-10-07T09:31:00+08:00', %s, 1),
                    (%s, 'n3', %s, %s, NULL, 'pending', NULL, 1,
                     NULL, NULL, NULL)
                """,
                (
                    shift_id, editor_id, editor_id,
                    shift_id, editor_id, editor_id, finalized_batch,
                    shift_id, editor_id, editor_id,
                ),
            )

            def fetch_row(article_id: str) -> dict[str, Any]:
                cur.execute(
                    "SELECT decision, rank, decided_at, version, "
                    "updated_by_user_id, report_type, finalized_batch_id "
                    "FROM shift_reviews WHERE article_id = %s",
                    (article_id,),
                )
                return dict(cur.fetchone())

            # dry_run：pending 的 n3 不命中；已定稿的 n2 计入 matched
            # （与批量放弃口径一致），执行时才会被跳过
            result = db_postgres_shift_reviews.bulk_restore_shift_reviews(
                cur,
                shift_id=shift_id,
                actor_user_id=editor_id,
                dry_run=True,
            )
            assert result == {"matched": 2, "updated": 0}
            assert fetch_row("n1")["decision"] == "discarded"

            result = db_postgres_shift_reviews.bulk_restore_shift_reviews(
                cur,
                shift_id=shift_id,
                actor_user_id=editor_id,
                dry_run=False,
            )
            # 一条匹配行（已定稿的 n2）被跳过：matched 必须大于 updated
            assert result == {"matched": 2, "updated": 1}
            assert result["matched"] > result["updated"]

            n1 = fetch_row("n1")
            assert n1["decision"] == "pending"
            assert n1["decided_at"] is None
            assert n1["rank"] is None
            assert n1["version"] == 3
            assert str(n1["updated_by_user_id"]) == editor_id
            # report_type 保持不变
            assert n1["report_type"] == "wanbao"

            # 已定稿行与 pending 行都不受影响
            n2 = fetch_row("n2")
            assert n2["decision"] == "discarded"
            assert n2["version"] == 1
            assert str(n2["finalized_batch_id"]) == str(finalized_batch)
            assert fetch_row("n3")["decision"] == "pending"
