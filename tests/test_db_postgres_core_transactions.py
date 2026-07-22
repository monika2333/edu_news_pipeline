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
    )

    assert transaction_events == ["begin", "commit"]
    assert calls == [("score", cursor), ("enqueue", cursor)]
