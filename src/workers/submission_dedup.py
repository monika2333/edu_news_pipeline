from __future__ import annotations

import hashlib
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from src.adapters.db_postgres_core import get_adapter
from src.adapters.title_cluster import (
    encode_texts,
    pack_embedding as _pack_embedding,
    unpack_embedding as _unpack_embedding,
)
from src.domain.submission_archive_config import (
    DEDUP_TOP_K,
    EMBED_BODY_CHARS,
    EMBED_MODEL,
    dedup_lookback_days,
    dedup_recall_threshold,
)
from src.workers import log_info, log_summary, worker_session

WORKER = "submission_dedup"


def _embedding_text(title: str, body: str) -> str:
    return f"{title}\n{body[:EMBED_BODY_CHARS]}"


def _embedding_source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def backfill_archive_embeddings(*, batch_size: int = 128) -> int:
    adapter = get_adapter()
    total = 0
    while True:
        items = adapter.submission_archive.fetch_items_missing_embeddings(
            limit=batch_size,
        )
        if not items:
            break
        texts = [
            _embedding_text(
                str(item.get("title") or ""),
                str(item.get("body") or ""),
            )
            for item in items
        ]
        vectors = encode_texts(texts)
        payload = [
            {
                "item_id": str(item["id"]),
                "embedding": _pack_embedding(vector),
                "embedding_model": EMBED_MODEL,
            }
            for item, vector in zip(items, vectors)
        ]
        updated = adapter.submission_archive.update_item_embeddings(payload)
        total += updated
        if updated == 0 or len(items) < batch_size:
            break
    return total


def _validate_archive_vectors(
    rows: Sequence[Mapping[str, Any]],
) -> np.ndarray:
    models = {
        str(row.get("embedding_model") or "")
        for row in rows
    }
    if models != {EMBED_MODEL}:
        raise RuntimeError(
            "存档向量模型与当前模型不一致，必须清空向量并重新补齐后再查重"
        )
    vectors = [_unpack_embedding(row["embedding"]) for row in rows]
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1:
        raise RuntimeError("存档向量维度不一致，无法安全查重")
    return np.stack(vectors)


def _prepare_news_vectors(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[np.ndarray, list[dict[str, Any]], int]:
    cached_models = {
        str(row.get("dedup_embedding_model") or "")
        for row in rows
        if row.get("dedup_embedding") is not None
    }
    if cached_models and cached_models != {EMBED_MODEL}:
        raise RuntimeError(
            "新闻查重向量模型与当前模型不一致，必须清空向量并重新生成后再查重"
        )

    vectors: list[Optional[np.ndarray]] = [None] * len(rows)
    stale_indices: list[int] = []
    stale_texts: list[str] = []
    source_hashes: list[str] = []
    reused = 0
    for index, row in enumerate(rows):
        text = _embedding_text(
            str(row.get("title") or ""),
            str(row.get("body") or ""),
        )
        source_hash = _embedding_source_hash(text)
        source_hashes.append(source_hash)
        embedding = row.get("dedup_embedding")
        if embedding is not None and row.get("dedup_source_hash") == source_hash:
            vectors[index] = _unpack_embedding(bytes(embedding))
            reused += 1
            continue
        stale_indices.append(index)
        stale_texts.append(text)

    updates: list[dict[str, Any]] = []
    if stale_indices:
        encoded = np.asarray(encode_texts(stale_texts), dtype=np.float32)
        if encoded.ndim != 2 or len(encoded) != len(stale_indices):
            raise RuntimeError("新闻查重向量编码结果数量或维度无效")
        for index, vector in zip(stale_indices, encoded):
            vectors[index] = vector
            updates.append(
                {
                    "article_id": str(rows[index]["article_id"]),
                    "embedding": _pack_embedding(vector),
                    "embedding_model": EMBED_MODEL,
                    "source_hash": source_hashes[index],
                }
            )

    resolved = [vector for vector in vectors if vector is not None]
    if len(resolved) != len(rows):
        raise RuntimeError("新闻查重向量未完整生成")
    dimensions = {len(vector) for vector in resolved}
    if len(dimensions) != 1:
        raise RuntimeError("新闻查重向量维度不一致，无法安全查重")
    return np.stack(resolved), updates, reused


def _build_matches(
    news_rows: Sequence[Mapping[str, Any]],
    archive_rows: Sequence[Mapping[str, Any]],
    news_vectors: np.ndarray,
    archive_matrix: np.ndarray,
    *,
    threshold: float,
) -> list[dict[str, Any]]:
    similarities = news_vectors @ archive_matrix.T
    matches: dict[tuple[str, str], dict[str, Any]] = {}
    for news_index, news in enumerate(news_rows):
        article_id = str(news["article_id"])
        exact_item_ids = {
            str(row["item_id"])
            for row in archive_rows
            if row.get("linked_article_id") == article_id
        }
        for item_id in exact_item_ids:
            matches[(article_id, item_id)] = {
                "article_id": article_id,
                "item_id": item_id,
                "similarity": 1.0,
                "match_method": "exact",
                "state": "confirmed",
            }

        row_scores = similarities[news_index]
        top_indices = np.argsort(row_scores)[::-1][:DEDUP_TOP_K]
        for archive_index in top_indices:
            similarity = float(row_scores[archive_index])
            if similarity < threshold:
                continue
            item_id = str(archive_rows[int(archive_index)]["item_id"])
            key = (article_id, item_id)
            if key in matches:
                continue
            matches[key] = {
                "article_id": article_id,
                "item_id": item_id,
                "similarity": similarity,
                "match_method": "vector",
                "state": "suspected",
            }
    return list(matches.values())


def run(limit: Optional[int] = None) -> dict[str, int]:
    adapter = get_adapter()
    with worker_session(WORKER, limit=limit):
        embedded = backfill_archive_embeddings()
        archive_rows = adapter.submission_archive.fetch_embeddings(
            lookback_days=dedup_lookback_days(),
        )
        if not archive_rows:
            log_info(WORKER, "No archive embeddings in the active window.")
            log_summary(WORKER, ok=0, failed=0)
            return {"embedded": embedded, "news": 0, "matches": 0}

        archive_matrix = _validate_archive_vectors(archive_rows)
        news_rows = adapter.submission_archive.fetch_news_for_dedup(limit=limit)
        if not news_rows:
            log_info(WORKER, "No current news ready for submission dedup.")
            log_summary(WORKER, ok=0, failed=0)
            return {"embedded": embedded, "news": 0, "matches": 0}

        news_vectors, news_embedding_updates, reused = _prepare_news_vectors(
            news_rows,
        )
        if news_embedding_updates:
            updated = adapter.news_summaries.update_dedup_embeddings(
                news_embedding_updates,
            )
            if updated != len(news_embedding_updates):
                raise RuntimeError(
                    "新闻查重向量未全部持久化，已终止本轮查重"
                )
        matches = _build_matches(
            news_rows,
            archive_rows,
            news_vectors,
            archive_matrix,
            threshold=dedup_recall_threshold(),
        )
        persisted = adapter.submission_archive.upsert_duplicate_matches(matches)
        log_info(
            WORKER,
            f"Compared {len(news_rows)} news rows with "
            f"{len(archive_rows)} archive items.",
        )
        log_info(
            WORKER,
            f"News embedding cache: encoded={len(news_embedding_updates)}, "
            f"reused={reused}.",
        )
        log_summary(WORKER, ok=len(news_rows), failed=0)
        return {
            "embedded": embedded,
            "news": len(news_rows),
            "news_embedded": len(news_embedding_updates),
            "news_reused": reused,
            "matches": persisted,
        }


__all__ = [
    "_build_matches",
    "_embedding_source_hash",
    "_pack_embedding",
    "_prepare_news_vectors",
    "_unpack_embedding",
    "backfill_archive_embeddings",
    "run",
]
