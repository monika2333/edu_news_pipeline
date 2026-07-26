from __future__ import annotations

from typing import Any

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
