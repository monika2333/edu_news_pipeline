from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import pytest

from src.adapters import db_postgres_core


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
