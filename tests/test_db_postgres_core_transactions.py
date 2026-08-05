from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from src.adapters import db_postgres_core


def test_connection_uses_bounded_connect_and_keepalive_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed: list[object] = []
    connect_kwargs: dict[str, object] = {}

    class FakeCursor:
        def __enter__(self) -> "FakeCursor":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def execute(self, query: object) -> None:
            executed.append(query)

    class FakeConnection:
        closed = False

        def cursor(self) -> FakeCursor:
            return FakeCursor()

    def fake_connect(**kwargs: object) -> FakeConnection:
        connect_kwargs.update(kwargs)
        return FakeConnection()

    monkeypatch.setattr(db_postgres_core, "_CONNECTION", None)
    monkeypatch.setattr(db_postgres_core.psycopg, "connect", fake_connect)
    monkeypatch.setattr(
        db_postgres_core,
        "get_settings",
        lambda: SimpleNamespace(
            db_host="db.example",
            db_port=5432,
            db_user="user",
            db_password="password",
            db_name="edu",
            db_schema="public",
        ),
    )

    connection = db_postgres_core._get_connection()

    assert isinstance(connection, FakeConnection)
    assert connect_kwargs == {
        "host": "db.example",
        "port": 5432,
        "user": "user",
        "password": "password",
        "dbname": "edu",
        "autocommit": True,
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }
    assert len(executed) == 1


def test_cluster_transaction_sets_local_statement_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    queries: list[str] = []

    class FakeCursor:
        def execute(self, query: str) -> None:
            queries.append(query)

    cursor = FakeCursor()

    @contextmanager
    def fake_transaction() -> Iterator[FakeCursor]:
        yield cursor

    monkeypatch.setattr(adapter, "transaction", fake_transaction)

    with adapter._cluster_transaction() as cluster_cursor:
        assert cluster_cursor is cursor

    assert queries == ["SET LOCAL statement_timeout = '120s'"]


def test_complete_external_filter_scores_and_enqueues_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    transaction_events: list[str] = []
    calls: list[tuple[str, object]] = []
    completed_at = datetime(2026, 7, 22, tzinfo=timezone.utc)

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        transaction_events.append("begin")
        yield cursor
        transaction_events.append("commit")

    def fake_complete_external_filter(cur: object, article_id: str, **kwargs: Any) -> datetime:
        calls.append(("score", cur))
        assert article_id == "article-1"
        assert kwargs["score"] == 80
        return completed_at

    def fake_enqueue_manual_review(cur: object, article_id: str, **kwargs: Any) -> None:
        calls.append(("enqueue", cur))
        assert article_id == "article-1"
        assert kwargs["status"] == "pending"

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.process,
        "complete_external_filter",
        fake_complete_external_filter,
    )
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "enqueue_manual_review",
        fake_enqueue_manual_review,
    )

    adapter.complete_external_filter(
        "article-1",
        passed=True,
        score=80,
        raw_output="80",
        category="internal_positive",
        prompt_key="internal_positive",
        prompt_version="v1",
    )

    assert transaction_events == ["begin", "commit"]
    assert calls == [("score", cursor), ("enqueue", cursor)]


def test_delete_console_user_preserves_history_and_clears_active_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    before = {
        "id": "editor-id",
        "role": "duty_editor",
        "is_active": True,
    }
    after = {
        **before,
        "is_active": False,
        "deleted_at": datetime.now(timezone.utc),
    }

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.users,
        "fetch_console_user_for_update",
        lambda cur, user_id: before,
    )
    monkeypatch.setattr(
        db_postgres_core.users,
        "fetch_future_shifts_for_user",
        lambda cur, user_id: [],
    )
    monkeypatch.setattr(
        db_postgres_core.users,
        "delete_duty_schedules_for_user",
        lambda cur, user_id: events.append("schedule-cleared"),
    )
    monkeypatch.setattr(
        db_postgres_core.users,
        "soft_delete_console_user",
        lambda cur, user_id: after,
    )
    monkeypatch.setattr(
        db_postgres_core.users,
        "revoke_console_user_sessions",
        lambda cur, user_id: events.append("sessions-revoked"),
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append(kwargs["action"]),
    )

    result = adapter.delete_console_user(
        user_id="editor-id",
        actor_user_id="admin-id",
    )

    assert result == after
    assert events == [
        "begin",
        "schedule-cleared",
        "sessions-revoked",
        "user.delete",
        "commit",
    ]


def test_future_shift_error_lists_human_readable_dates() -> None:
    message = db_postgres_core._future_shift_error_message(
        "删除",
        {
            "username": "monday",
            "display_name": "周一值班编辑",
        },
        [
            {
                "starts_at": datetime(2026, 7, 28, 14, tzinfo=timezone.utc),
                "ends_at": datetime(2026, 7, 29, 14, tzinfo=timezone.utc),
            },
            {
                "starts_at": datetime(2026, 8, 4, 14, tzinfo=timezone.utc),
                "ends_at": datetime(2026, 8, 5, 14, tzinfo=timezone.utc),
            }
        ],
    )

    assert message == (
        "无法删除“周一值班编辑”：仍负责以下未来班次："
        "7月29日、8月5日。请先改派或取消这些班次。"
    )


def test_admin_discard_and_audit_share_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    before = {
        "article_id": "article-1",
        "admin_discarded_at": None,
    }
    after = {
        "article_id": "article-1",
        "admin_discarded_at": datetime.now(timezone.utc),
    }

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "set_admin_discarded",
        lambda cur, **kwargs: (before, after),
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append(kwargs["action"]),
    )

    result = adapter.set_shift_review_admin_discarded(
        shift_id="shift-1",
        article_id="article-1",
        actor_user_id="admin-1",
        discarded=True,
    )

    assert result == after
    assert events == ["begin", "duty_summary.discard", "commit"]


def test_bulk_admin_discard_uses_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "set_admin_discarded",
        lambda cur, **kwargs: (
            {"article_id": kwargs["article_id"]},
            {"article_id": kwargs["article_id"], "admin_discarded_at": "now"},
        ),
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append(kwargs["action"]),
    )

    result = adapter.set_shift_reviews_admin_discarded(
        shift_id="shift-1",
        article_ids=["article-1", "article-2"],
        actor_user_id="admin-1",
        discarded=True,
    )

    assert [item["article_id"] for item in result] == ["article-1", "article-2"]
    assert events == [
        "begin",
        "duty_summary.discard",
        "duty_summary.discard",
        "commit",
    ]


def test_bulk_shift_review_update_uses_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    article_ids = [
        "article-1",
        "chinanews:/sh/2026/07-27/10666981",
    ]

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    def fake_upsert(cur: object, **kwargs: Any) -> tuple[None, dict[str, Any]]:
        assert cur is cursor
        events.append(f"save:{kwargs['article_id']}")
        return None, {
            "article_id": kwargs["article_id"],
            "version": 1,
        }

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "shift_contains_article",
        lambda cur, **kwargs: True,
    )
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "upsert_shift_review",
        fake_upsert,
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append(kwargs["action"]),
    )

    result = adapter.save_shift_reviews(
        shift_id="shift-1",
        actor_user_id="editor-1",
        updates=[
            {
                "article_id": article_id,
                "expected_version": 0,
                "patch": {"decision": "selected"},
            }
            for article_id in article_ids
        ],
        action="shift_review.decide",
    )

    assert [item["article_id"] for item in result] == article_ids
    assert events == [
        "begin",
        "save:article-1",
        "save:chinanews:/sh/2026/07-27/10666981",
        "shift_review.decide",
        "commit",
    ]


def test_shift_bulk_discard_and_audit_share_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    captured: dict[str, Any] = {}

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    def fake_bulk_discard(cur: object, **kwargs: Any) -> dict[str, int]:
        assert cur is cursor
        captured.update(kwargs)
        events.append("discard")
        return {"matched": 3, "updated": 2, "skipped_finalized": 1}

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "bulk_discard_shift_candidates",
        fake_bulk_discard,
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append(kwargs["action"]),
    )

    result = adapter.discard_shift_candidates_as_user(
        shift_id="shift-1",
        actor_user_id="editor-1",
        region="internal",
        sentiment="positive",
        query=None,
        published_before=None,
        report_type="zongbao",
        dry_run=False,
        request_id="request-1",
    )

    assert result == {"matched": 3, "updated": 2, "skipped_finalized": 1}
    assert captured["dry_run"] is False
    assert events == [
        "begin",
        "discard",
        "shift_review.bulk_discard",
        "commit",
    ]


def test_shift_bulk_discard_dry_run_does_not_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "bulk_discard_shift_candidates",
        lambda cur, **kwargs: {
            "matched": 0,
            "updated": 0,
            "skipped_finalized": 0,
        },
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append("audit"),
    )

    result = adapter.discard_shift_candidates_as_user(
        shift_id="shift-1",
        actor_user_id="editor-1",
        region="internal",
        sentiment="positive",
        query=None,
        published_before=None,
        report_type="zongbao",
        dry_run=True,
    )

    assert result == {"matched": 0, "updated": 0, "skipped_finalized": 0}
    assert events == ["begin", "commit"]


def test_bulk_shift_review_update_rolls_back_after_late_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        try:
            yield cursor
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    def fake_upsert(cur: object, **kwargs: Any) -> tuple[None, dict[str, Any]]:
        article_id = kwargs["article_id"]
        events.append(f"save:{article_id}")
        if article_id == "article-2":
            raise db_postgres_core.shift_reviews.ShiftReviewConflictError(
                "Review version is stale"
            )
        return None, {"article_id": article_id, "version": 1}

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "shift_contains_article",
        lambda cur, **kwargs: True,
    )
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "upsert_shift_review",
        fake_upsert,
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append("audit"),
    )

    with pytest.raises(
        db_postgres_core.shift_reviews.ShiftReviewConflictError,
        match="stale",
    ):
        adapter.save_shift_reviews(
            shift_id="shift-1",
            actor_user_id="editor-1",
            updates=[
                {
                    "article_id": "article-1",
                    "expected_version": 0,
                    "patch": {"decision": "selected"},
                },
                {
                    "article_id": "article-2",
                    "expected_version": 0,
                    "patch": {"decision": "selected"},
                },
            ],
            action="shift_review.decide",
        )

    assert events == [
        "begin",
        "save:article-1",
        "save:article-2",
        "rollback",
    ]


def test_shift_review_order_and_categories_share_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    category_updates = [
        {
            "article_id": "article-1",
            "is_beijing_related": True,
            "sentiment_label": "positive",
        }
    ]

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.shift_reviews,
        "update_shift_review_order",
        lambda cur, **kwargs: events.append("order") or 1,
    )
    monkeypatch.setattr(
        db_postgres_core.news_summaries,
        "update_summary_categories",
        lambda cur, updates: events.append("categories") or len(updates),
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: events.append(kwargs["action"]),
    )

    updated = adapter.update_shift_review_order(
        shift_id="shift-1",
        actor_user_id="editor-1",
        selected_order=["article-1"],
        backup_order=[],
        category_updates=category_updates,
    )

    assert updated == 1
    assert events == [
        "begin",
        "order",
        "categories",
        "shift_review.reorder",
        "commit",
    ]
