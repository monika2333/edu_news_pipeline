from __future__ import annotations

from typing import Any

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

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)
        self.last_query = query

    def fetchone(self) -> dict[str, Any]:
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
