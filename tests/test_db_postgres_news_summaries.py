from __future__ import annotations

from typing import Any

from src.adapters.db_postgres_news_summaries import (
    fetch_news_summary_content,
    insert_pending_summary,
    update_dedup_embeddings,
    upsert_news_summaries_from_primary,
    upsert_news_summary,
)


class FakeCursor:
    def __init__(self, row: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.executemany_calls: list[tuple[str, Any]] = []
        self.row = row
        self.rowcount = 0

    def execute(self, query: str, params: Any) -> None:
        self.calls.append((query, params))

    def fetchone(self) -> dict[str, Any] | None:
        return self.row

    def executemany(self, query: str, params: Any) -> None:
        values = list(params)
        self.executemany_calls.append((query, values))
        self.rowcount = len(values)


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


def test_update_dedup_embeddings_only_updates_cache_columns() -> None:
    cursor = FakeCursor()

    updated = update_dedup_embeddings(
        cursor,
        [
            {
                "article_id": "article-1",
                "embedding": b"vector",
                "embedding_model": "model",
                "source_hash": "hash",
            }
        ],
    )

    assert updated == 1
    query, params = cursor.executemany_calls[0]
    assert "dedup_embedding = %s" in query
    assert "dedup_embedding_model = %s" in query
    assert "dedup_source_hash = %s" in query
    assert "dedup_embedded_at = NOW()" in query
    assert params == [(b"vector", "model", "hash", "article-1")]


def test_summary_upserts_do_not_overwrite_dedup_cache_columns() -> None:
    cursor = FakeCursor()
    article = {
        "article_id": "article-1",
        "title": "Test title",
        "content_markdown": "Test content",
    }

    upsert_news_summary(cursor, article, "Generated summary")
    upsert_news_summaries_from_primary(
        cursor,
        [
            {
                **article,
                "score": 80,
                "raw_relevance_score": 80,
                "keyword_bonus_score": 0,
                "score_details": {},
                "status": "pending",
                "keywords": [],
            }
        ],
    )

    queries = [cursor.calls[0][0], cursor.executemany_calls[0][0]]
    for query in queries:
        assert "dedup_embedding" not in query
        assert "dedup_embedding_model" not in query
        assert "dedup_source_hash" not in query
        assert "dedup_embedded_at" not in query
