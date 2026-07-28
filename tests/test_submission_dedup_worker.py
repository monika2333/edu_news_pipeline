from __future__ import annotations

import numpy as np

from src.workers.submission_dedup import (
    _build_matches,
    _pack_embedding,
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
