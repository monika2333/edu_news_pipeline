from __future__ import annotations

from typing import Any

from src.adapters.db_postgres_news_summaries import insert_pending_summary


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Any]]] = []

    def execute(self, query: str, params: list[Any]) -> None:
        self.calls.append((query, params))


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
