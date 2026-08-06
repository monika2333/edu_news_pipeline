from __future__ import annotations

from typing import Any

from src.adapters.db_postgres_news_summaries import (
    fetch_news_summary_content,
    insert_pending_summary,
)


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.row = row

    def execute(self, query: str, params: Any) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, Any] | None:
        return self.row


def test_insert_pending_summary_only_executes_summary_upsert() -> None:
    cursor = FakeCursor()

    insert_pending_summary(
        cursor,
        {
            "article_id": "article-1",
            "title": "Test title",
            "content_markdown": "Test content",
        },
        keywords=["education", "education"],
    )

    assert len(cursor.calls) == 1
    query, params = cursor.calls[0]
    assert "INSERT INTO news_summaries" in query
    assert params[0] == "article-1"
    assert params[-1] == ["education"]


def test_fetch_news_summary_content_selects_drawer_metadata() -> None:
    expected = {
        "article_id": "article-1",
        "title": "Test title",
        "source": "Test source",
        "url": "https://example.com/article-1",
        "created_at": "2026-08-06T10:00:00+00:00",
        "content_markdown": "First paragraph.\n\nSecond paragraph.",
    }
    cursor = FakeCursor(expected)

    result = fetch_news_summary_content(cursor, "article-1")

    assert result == expected
    query, params = cursor.calls[0]
    for column in (
        "article_id",
        "title",
        "source",
        "url",
        "created_at",
        "content_markdown",
    ):
        assert column in query
    assert "publish_time_iso" not in query
    assert "fetched_at" not in query
    assert params == ("article-1",)
