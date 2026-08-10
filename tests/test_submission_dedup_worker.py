from __future__ import annotations

import numpy as np
import pytest

from src.domain.submission_archive_config import EMBED_MODEL
from src.workers import submission_dedup
from src.workers.submission_dedup import (
    _build_matches,
    _embedding_source_hash,
    _pack_embedding,
    _prepare_news_vectors,
    _unpack_embedding,
)


def test_embedding_binary_round_trip_uses_little_endian_float32() -> None:
    packed = _pack_embedding([0.25, -0.5, 1.0])
    unpacked = _unpack_embedding(packed)

    assert unpacked.dtype == np.dtype("<f4")
    assert np.allclose(unpacked, [0.25, -0.5, 1.0])


def test_build_matches_records_exact_and_top_vector_candidates() -> None:
    news = [
        {
            "article_id": "article-1",
            "title": "标题",
            "body": "正文",
        }
    ]
    archive = [
        {
            "item_id": "exact-item",
            "linked_article_id": "article-1",
        },
        {
            "item_id": "vector-item",
            "linked_article_id": None,
        },
        {
            "item_id": "below-threshold",
            "linked_article_id": None,
        },
    ]
    news_vectors = np.asarray([[1.0, 0.0]], dtype=np.float32)
    archive_matrix = np.asarray(
        [
            [1.0, 0.0],
            [0.8, 0.6],
            [0.0, 1.0],
        ],
        dtype=np.float32,
    )

    matches = _build_matches(
        news,
        archive,
        news_vectors,
        archive_matrix,
        threshold=0.72,
    )
    by_item = {match["item_id"]: match for match in matches}

    assert by_item["exact-item"]["state"] == "confirmed"
    assert by_item["exact-item"]["match_method"] == "exact"
    assert by_item["vector-item"]["state"] == "suspected"
    assert "below-threshold" not in by_item


def test_prepare_news_vectors_reuses_matching_hash_and_encodes_only_stale_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unchanged_text = submission_dedup._embedding_text("未修改", "原摘要")
    rows = [
        {
            "article_id": "article-1",
            "title": "未修改",
            "body": "原摘要",
            "dedup_embedding": _pack_embedding([1.0, 0.0]),
            "dedup_embedding_model": EMBED_MODEL,
            "dedup_source_hash": _embedding_source_hash(unchanged_text),
        },
        {
            "article_id": "article-2",
            "title": "已修改",
            "body": "新摘要",
            "dedup_embedding": _pack_embedding([0.5, 0.5]),
            "dedup_embedding_model": EMBED_MODEL,
            "dedup_source_hash": "old-hash",
        },
    ]
    encoded_texts: list[str] = []

    def fake_encode(texts: list[str]) -> np.ndarray:
        encoded_texts.extend(texts)
        return np.asarray([[0.0, 1.0]], dtype=np.float32)

    monkeypatch.setattr(submission_dedup, "encode_texts", fake_encode)

    matrix, updates, reused = _prepare_news_vectors(rows)

    assert encoded_texts == [submission_dedup._embedding_text("已修改", "新摘要")]
    assert reused == 1
    assert np.allclose(matrix, [[1.0, 0.0], [0.0, 1.0]])
    assert len(updates) == 1
    assert updates[0]["article_id"] == "article-2"
    assert updates[0]["embedding_model"] == EMBED_MODEL
    assert updates[0]["source_hash"] == _embedding_source_hash(encoded_texts[0])


def test_prepare_news_vectors_rejects_mismatched_cached_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = {
        "article_id": "article-1",
        "title": "标题",
        "body": "摘要",
        "dedup_embedding": _pack_embedding([1.0, 0.0]),
        "dedup_embedding_model": "different-model",
        "dedup_source_hash": "stale-hash",
    }

    def fail_encode(texts: list[str]) -> np.ndarray:
        raise AssertionError(f"must not encode after model mismatch: {texts}")

    monkeypatch.setattr(submission_dedup, "encode_texts", fail_encode)

    with pytest.raises(RuntimeError, match="新闻查重向量模型与当前模型不一致"):
        _prepare_news_vectors([row])
