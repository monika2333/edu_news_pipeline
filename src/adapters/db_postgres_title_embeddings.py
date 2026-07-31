from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

import psycopg

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter


class TitleEmbeddingsNamespace:
    """Single-table access to cached news title embeddings."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def fetch(self, article_ids: Sequence[str]) -> list[dict[str, Any]]:
        with self._adapter._cluster_transaction() as cur:
            return fetch_title_embeddings(cur, article_ids)

    def upsert(self, embeddings: Sequence[Mapping[str, Any]]) -> int:
        with self._adapter._cluster_transaction() as cur:
            return upsert_title_embeddings(cur, embeddings)


def fetch_title_embeddings(
    cur: psycopg.Cursor,
    article_ids: Sequence[str],
) -> list[dict[str, Any]]:
    normalized_ids = list(
        dict.fromkeys(
            str(article_id).strip()
            for article_id in article_ids
            if str(article_id).strip()
        )
    )
    if not normalized_ids:
        return []
    cur.execute(
        """
        select article_id, embedding, model, title_hash, updated_at
        from news_title_embeddings
        where article_id = any(%s)
        """,
        (normalized_ids,),
    )
    return [dict(row) for row in cur.fetchall()]


def upsert_title_embeddings(
    cur: psycopg.Cursor,
    embeddings: Sequence[Mapping[str, Any]],
) -> int:
    payload = [
        (
            str(item["article_id"]),
            item["embedding"],
            str(item["model"]),
            str(item["title_hash"]),
        )
        for item in embeddings
    ]
    if not payload:
        return 0
    cur.executemany(
        """
        insert into news_title_embeddings (
            article_id,
            embedding,
            model,
            title_hash
        )
        values (%s, %s, %s, %s)
        on conflict (article_id) do update
        set embedding = excluded.embedding,
            model = excluded.model,
            title_hash = excluded.title_hash,
            updated_at = now()
        """,
        payload,
    )
    return max(0, int(cur.rowcount or 0))


__all__ = [
    "TitleEmbeddingsNamespace",
    "fetch_title_embeddings",
    "upsert_title_embeddings",
]
