from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterator, Optional

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


def test_clear_review_buckets_for_owner_uses_versioned_updates_and_one_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    targets = [
        {
            "article_id": "selected-1",
            "version": 3,
            "previous_status": "selected",
            "report_type": "wanbao",
        },
        {
            "article_id": "backup-1",
            "version": 5,
            "previous_status": "backup",
            "report_type": "zongbao",
        },
    ]
    before = [
        {"article_id": "selected-1", "status": "selected"},
        {"article_id": "backup-1", "status": "backup"},
    ]
    after = [
        {
            "article_id": "selected-1",
            "status": "discarded",
            "rank": None,
            "report_type": "wanbao",
        },
        {
            "article_id": "backup-1",
            "status": "discarded",
            "rank": None,
            "report_type": "zongbao",
        },
    ]
    audit_calls: list[dict[str, Any]] = []

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    def fake_update(
        cur: object,
        updates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        assert cur is cursor
        assert updates == [
            {"article_id": "selected-1", "status": "discarded", "rank": None},
            {"article_id": "backup-1", "status": "discarded", "rank": None},
        ]
        assert kwargs == {
            "owner_user_id": "admin-1",
            "actor_username": "system:scheduled_clear",
            "actor_user_id": "admin-1",
            "expected_versions": {"selected-1": 3, "backup-1": 5},
            "require_versions": True,
            "report_type": None,
        }
        events.append("update")
        return before, after

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "fetch_review_buckets_for_update",
        lambda cur, *, owner_user_id: (
            events.append(f"fetch:{owner_user_id}") or targets
        ),
    )
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "update_manual_review_statuses_with_versions",
        fake_update,
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: audit_calls.append(kwargs),
    )

    result = adapter.clear_review_buckets_for_owner_as_user(
        owner_user_id="admin-1",
        actor_username="system:scheduled_clear",
        actor_user_id="admin-1",
        trigger="scheduled",
        request_id="request-1",
    )

    assert events == ["begin", "fetch:admin-1", "update", "commit"]
    assert [row["previous_status"] for row in result] == ["selected", "backup"]
    assert len(audit_calls) == 1
    assert audit_calls[0] == {
        "actor_user_id": "admin-1",
        "action": "manual_review.clear_buckets",
        "target_type": "manual_review_batch",
        "target_id": "admin-1",
        "before_data": {"items": before},
        "after_data": {"items": after, "trigger": "scheduled"},
        "request_id": "request-1",
    }


def test_discard_manual_candidates_uses_versioned_updates_and_one_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    targets = [
        {"article_id": "article-1", "version": 3},
        {"article_id": "article-2", "version": 7},
    ]
    before = [
        {"article_id": "article-1", "status": "pending", "version": 3},
        {"article_id": "article-2", "status": "pending", "version": 7},
    ]
    after = [
        {
            "article_id": "article-1",
            "status": "discarded",
            "rank": None,
            "report_type": "wanbao",
            "version": 4,
        },
        {
            "article_id": "article-2",
            "status": "discarded",
            "rank": None,
            "report_type": "wanbao",
            "version": 8,
        },
    ]
    audit_calls: list[dict[str, Any]] = []

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    def fake_fetch(cur: object, **kwargs: Any) -> list[dict[str, Any]]:
        assert cur is cursor
        assert kwargs == {
            "region": "external",
            "owner_user_id": "admin-1",
            "sentiment": "negative",
            "query": "keyword",
            "created_before": datetime(2026, 9, 1, tzinfo=timezone.utc).date(),
            "report_type": "wanbao",
            "duty_unprocessed_only": False,
        }
        events.append("fetch")
        return targets

    def fake_update(
        cur: object,
        updates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        assert cur is cursor
        assert updates == [
            {
                "article_id": "article-1",
                "status": "discarded",
                "rank": None,
                "report_type": "wanbao",
            },
            {
                "article_id": "article-2",
                "status": "discarded",
                "rank": None,
                "report_type": "wanbao",
            },
        ]
        assert kwargs == {
            "owner_user_id": "admin-1",
            "actor_username": "admin-user",
            "actor_user_id": "admin-1",
            "expected_versions": {"article-1": 3, "article-2": 7},
            "require_versions": True,
            "report_type": "wanbao",
        }
        events.append("update")
        return before, after

    def fake_audit(cur: object, **kwargs: Any) -> None:
        assert cur is cursor
        events.append("audit")
        audit_calls.append(kwargs)

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "fetch_manual_candidates_before_date_for_update",
        fake_fetch,
    )
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "update_manual_review_statuses_with_versions",
        fake_update,
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        fake_audit,
    )

    result = adapter.discard_manual_candidates_before_date_as_user(
        region="external",
        sentiment="negative",
        query="keyword",
        created_before=datetime(2026, 9, 1, tzinfo=timezone.utc).date(),
        report_type="wanbao",
        actor_username="admin-user",
        actor_user_id="admin-1",
        request_id="request-1",
    )

    assert result == after
    assert events == ["begin", "fetch", "update", "audit", "commit"]
    assert audit_calls == [
        {
            "actor_user_id": "admin-1",
            "action": "manual_review.bulk_discard",
            "target_type": "manual_review_batch",
            "target_id": "wanbao",
            "before_data": {"items": before},
            "after_data": {"items": after},
            "request_id": "request-1",
        }
    ]


def test_discard_manual_candidates_does_not_audit_empty_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    events: list[str] = []
    audit_calls: list[dict[str, Any]] = []

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        events.append("begin")
        yield cursor
        events.append("commit")

    def fake_fetch(cur: object, **kwargs: Any) -> list[dict[str, Any]]:
        assert cur is cursor
        events.append("fetch")
        return []

    def fake_update(
        cur: object,
        updates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        assert cur is cursor
        assert updates == []
        assert kwargs["expected_versions"] == {}
        events.append("update")
        return [], []

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "fetch_manual_candidates_before_date_for_update",
        fake_fetch,
    )
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "update_manual_review_statuses_with_versions",
        fake_update,
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: audit_calls.append(kwargs),
    )

    result = adapter.discard_manual_candidates_before_date_as_user(
        region="internal",
        sentiment="positive",
        query=None,
        created_before=None,
        report_type="zongbao",
        actor_username="admin-user",
        actor_user_id="admin-1",
    )

    assert result == []
    assert events == ["begin", "fetch", "update", "commit"]
    assert audit_calls == []


def test_clear_review_buckets_for_owner_does_not_audit_empty_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    cursor = object()
    audit_calls: list[dict[str, Any]] = []

    @contextmanager
    def fake_transaction() -> Iterator[object]:
        yield cursor

    adapter.transaction = fake_transaction
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "fetch_review_buckets_for_update",
        lambda cur, *, owner_user_id: [],
    )
    monkeypatch.setattr(
        db_postgres_core.manual_reviews,
        "update_manual_review_statuses_with_versions",
        lambda cur, updates, **kwargs: ([], []),
    )
    monkeypatch.setattr(
        db_postgres_core.audit,
        "insert_review_event",
        lambda cur, **kwargs: audit_calls.append(kwargs),
    )

    result = adapter.clear_review_buckets_for_owner_as_user(
        owner_user_id="admin-1",
        actor_username="system:scheduled_clear",
        actor_user_id="admin-1",
        trigger="scheduled",
    )

    assert result == []
    assert audit_calls == []


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


class ShiftReviewBatchCursor:
    def __init__(
        self,
        *,
        contained_article_ids: list[str],
        existing_rows: list[dict[str, Any]],
    ) -> None:
        self.contained_article_ids = set(contained_article_ids)
        self.existing_rows = {
            str(row["article_id"]): dict(row) for row in existing_rows
        }
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.last_query = ""
        self.next_row: Optional[dict[str, Any]] = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.last_query = query
        self.queries.append(query)
        self.params.append(params)
        if "INSERT INTO shift_reviews" in query:
            self.next_row = {
                "id": f"review-{params[1]}",
                "shift_id": params[0],
                "article_id": params[1],
                "created_by_user_id": params[2],
                "updated_by_user_id": params[3],
                "report_type": params[4],
                "decision": params[5],
                "rank": None,
                "excerpt_text": params[6],
                "edited_summary": params[7],
                "manual_llm_source": params[8],
                "notes": params[9],
                "version": 1,
                "finalized_batch_id": None,
                "finalized_rank": None,
            }
        elif "UPDATE shift_reviews" in query:
            review_id = str(params[-1])
            current = next(
                row
                for row in self.existing_rows.values()
                if str(row["id"]) == review_id
            )
            self.next_row = {
                **current,
                "report_type": params[0],
                "decision": params[1],
                "rank": None if params[2] else current.get("rank"),
                "excerpt_text": params[3],
                "edited_summary": params[4],
                "manual_llm_source": params[5],
                "notes": params[6],
                "updated_by_user_id": params[7],
                "version": int(current["version"]) + 1,
            }
        elif "INSERT INTO review_events" in query:
            self.next_row = {"id": 1}

    def fetchall(self) -> list[dict[str, Any]]:
        requested_ids = self.params[-1][1]
        if "FROM duty_shifts" in self.last_query:
            return [
                {"article_id": article_id}
                for article_id in sorted(requested_ids)
                if article_id in self.contained_article_ids
            ]
        if "FROM shift_reviews" in self.last_query:
            return [
                self.existing_rows[article_id]
                for article_id in sorted(requested_ids)
                if article_id in self.existing_rows
            ]
        raise AssertionError(f"Unexpected fetchall query: {self.last_query}")

    def fetchone(self) -> Optional[dict[str, Any]]:
        row = self.next_row
        self.next_row = None
        return row


def _shift_review_row(
    article_id: str,
    *,
    version: int = 2,
    finalized_batch_id: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": f"review-{article_id}",
        "shift_id": "shift-1",
        "article_id": article_id,
        "created_by_user_id": "editor-1",
        "updated_by_user_id": "editor-1",
        "report_type": "zongbao",
        "decision": "selected",
        "rank": 3,
        "excerpt_text": "原摘录",
        "edited_summary": "原摘要",
        "manual_llm_source": "原来源",
        "notes": "原备注",
        "version": version,
        "finalized_batch_id": finalized_batch_id,
        "finalized_rank": 1 if finalized_batch_id else None,
    }


@pytest.mark.parametrize("item_count", [1, 10])
def test_bulk_shift_review_update_uses_constant_queries_plus_one_per_item(
    item_count: int,
) -> None:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    article_ids = [f"article-{index:02d}" for index in range(item_count)]
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=article_ids,
        existing_rows=[],
    )
    events: list[str] = []

    @contextmanager
    def fake_transaction() -> Iterator[ShiftReviewBatchCursor]:
        events.append("begin")
        yield cursor
        events.append("commit")

    adapter.transaction = fake_transaction

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
    assert events == ["begin", "commit"]
    assert len(cursor.queries) == item_count + 3
    assert sum("FROM duty_shifts" in query for query in cursor.queries) == 1
    assert sum("FROM shift_reviews" in query for query in cursor.queries) == 1
    assert sum("INSERT INTO shift_reviews" in query for query in cursor.queries) == item_count
    lock_query = next(
        query for query in cursor.queries if "FROM shift_reviews" in query
    )
    assert "ORDER BY article_id" in lock_query
    assert "FOR UPDATE" in lock_query


def _adapter_with_shift_batch_cursor(
    cursor: ShiftReviewBatchCursor,
) -> tuple[db_postgres_core.PostgresAdapter, list[str]]:
    adapter = object.__new__(db_postgres_core.PostgresAdapter)
    events: list[str] = []

    @contextmanager
    def fake_transaction() -> Iterator[ShiftReviewBatchCursor]:
        events.append("begin")
        try:
            yield cursor
        except Exception:
            events.append("rollback")
            raise
        else:
            events.append("commit")

    adapter.transaction = fake_transaction
    return adapter, events


def test_bulk_shift_review_rejects_outside_article_before_any_write() -> None:
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=["article-1"],
        existing_rows=[],
    )
    adapter, events = _adapter_with_shift_batch_cursor(cursor)

    with pytest.raises(
        ValueError,
        match="Article does not belong to this active shift",
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
                    "article_id": "outside-article",
                    "expected_version": 0,
                    "patch": {"decision": "selected"},
                },
            ],
            action="shift_review.decide",
        )

    assert events == ["begin", "rollback"]
    assert len(cursor.queries) == 1
    assert "FROM duty_shifts" in cursor.queries[0]
    assert not any("shift_reviews" in query for query in cursor.queries)


def test_bulk_shift_review_rejects_duplicate_article_id_without_sql() -> None:
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=["article-1"],
        existing_rows=[],
    )
    adapter, events = _adapter_with_shift_batch_cursor(cursor)
    duplicate_update = {
        "article_id": "article-1",
        "expected_version": 0,
        "patch": {"decision": "selected"},
    }

    with pytest.raises(ValueError, match="more than once"):
        adapter.save_shift_reviews(
            shift_id="shift-1",
            actor_user_id="editor-1",
            updates=[duplicate_update, duplicate_update],
            action="shift_review.decide",
        )

    assert events == ["begin", "rollback"]
    assert cursor.queries == []


def test_bulk_shift_review_rejects_stale_existing_version() -> None:
    existing = _shift_review_row("article-1", version=2)
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=["article-1"],
        existing_rows=[existing],
    )
    adapter, events = _adapter_with_shift_batch_cursor(cursor)

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
                    "expected_version": 1,
                    "patch": {"edited_summary": "新摘要"},
                }
            ],
            action="shift_review.edit",
        )

    assert events == ["begin", "rollback"]
    assert len(cursor.queries) == 2


def test_bulk_shift_review_rejects_nonzero_version_for_new_row() -> None:
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=["article-1"],
        existing_rows=[],
    )
    adapter, events = _adapter_with_shift_batch_cursor(cursor)

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
                    "expected_version": 3,
                    "patch": {"decision": "selected"},
                }
            ],
            action="shift_review.decide",
        )

    assert events == ["begin", "rollback"]
    assert len(cursor.queries) == 2


def test_bulk_shift_review_rejects_finalized_existing_row() -> None:
    existing = _shift_review_row(
        "article-1",
        version=2,
        finalized_batch_id="batch-1",
    )
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=["article-1"],
        existing_rows=[existing],
    )
    adapter, events = _adapter_with_shift_batch_cursor(cursor)

    with pytest.raises(ValueError, match="先撤回"):
        adapter.save_shift_reviews(
            shift_id="shift-1",
            actor_user_id="editor-1",
            updates=[
                {
                    "article_id": "article-1",
                    "expected_version": 2,
                    "patch": {"edited_summary": "新摘要"},
                }
            ],
            action="shift_review.edit",
        )

    assert events == ["begin", "rollback"]
    assert len(cursor.queries) == 2


def test_bulk_shift_review_mixed_rows_preserve_request_and_audit_order() -> None:
    existing = _shift_review_row("article-a", version=2)
    requested_ids = ["article-z", "article-a"]
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=requested_ids,
        existing_rows=[existing],
    )
    adapter, events = _adapter_with_shift_batch_cursor(cursor)

    result = adapter.save_shift_reviews(
        shift_id="shift-1",
        actor_user_id="editor-1",
        updates=[
            {
                "article_id": "article-z",
                "expected_version": 0,
                "patch": {"manual_llm_source": "新来源"},
            },
            {
                "article_id": "article-a",
                "expected_version": 2,
                "patch": {"edited_summary": "新摘要"},
            },
        ],
        action="shift_review.edit",
        request_id="request-1",
    )

    assert events == ["begin", "commit"]
    assert [row["article_id"] for row in result] == requested_ids
    assert result[1]["edited_summary"] == "新摘要"
    assert result[1]["manual_llm_source"] == "原来源"
    audit_index = next(
        index
        for index, query in enumerate(cursor.queries)
        if "INSERT INTO review_events" in query
    )
    audit_params = cursor.params[audit_index]
    before_items = audit_params[4].obj["items"]
    after_items = audit_params[5].obj["items"]
    assert before_items == [None, existing]
    assert [row["article_id"] for row in after_items] == requested_ids
    assert audit_params[1:4] == (
        "shift_review.edit",
        "shift_review_batch",
        "shift-1",
    )
    assert audit_params[6] == "request-1"


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
        created_before=None,
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
        created_before=None,
        report_type="zongbao",
        dry_run=True,
    )

    assert result == {"matched": 0, "updated": 0, "skipped_finalized": 0}
    assert events == ["begin", "commit"]


def test_bulk_shift_review_update_rolls_back_after_late_failure() -> None:
    existing = _shift_review_row("article-2", version=2)
    cursor = ShiftReviewBatchCursor(
        contained_article_ids=["article-1", "article-2"],
        existing_rows=[existing],
    )
    adapter, events = _adapter_with_shift_batch_cursor(cursor)

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
                    "expected_version": 1,
                    "patch": {"decision": "selected"},
                },
            ],
            action="shift_review.decide",
        )

    assert events == ["begin", "rollback"]
    assert sum(
        "INSERT INTO shift_reviews" in query for query in cursor.queries
    ) == 1
    assert not any("INSERT INTO review_events" in query for query in cursor.queries)


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
