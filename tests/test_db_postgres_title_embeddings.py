from __future__ import annotations

from typing import Any

from src.adapters import db_postgres_title_embeddings


class FakeCursor:
    def __init__(self) -> None:
        self.query = ""
        self.params: tuple[Any, ...] = ()
        self.payload: list[tuple[Any, ...]] = []
        self.rows: list[dict[str, Any]] = []
        self.rowcount = 0

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params

    def executemany(
        self,
        query: str,
        payload: list[tuple[Any, ...]],
    ) -> None:
        self.query = query
        self.payload = payload
        self.rowcount = len(payload)

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


def test_fetch_title_embeddings_batches_and_deduplicates_ids() -> None:
    cursor = FakeCursor()
    cursor.rows = [
        {
            "article_id": "article-1",
            "embedding": b"vector",
            "model": "model",
            "title_hash": "hash",
        }
    ]

    rows = db_postgres_title_embeddings.fetch_title_embeddings(
        cursor,
        ["article-1", "article-1", "article-2"],
    )

    assert rows == cursor.rows
    assert "where article_id = any(%s)" in cursor.query
    assert cursor.params == (["article-1", "article-2"],)


def test_fetch_title_embeddings_skips_empty_query() -> None:
    cursor = FakeCursor()

    assert db_postgres_title_embeddings.fetch_title_embeddings(
        cursor,
        [],
    ) == []
    assert cursor.query == ""


def test_upsert_title_embeddings_is_idempotent() -> None:
    cursor = FakeCursor()

    updated = db_postgres_title_embeddings.upsert_title_embeddings(
        cursor,
        [
            {
                "article_id": "article-1",
                "embedding": b"vector",
                "model": "model",
                "title_hash": "hash",
            }
        ],
    )

    assert updated == 1
    assert "on conflict (article_id) do update" in cursor.query
    assert "updated_at = now()" in cursor.query
    assert cursor.payload == [
        ("article-1", b"vector", "model", "hash"),
    ]
