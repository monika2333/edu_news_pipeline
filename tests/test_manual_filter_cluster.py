from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pytest

from src.adapters.title_cluster import EMBEDDING_MODEL_NAME
from src.console import manual_filter_cluster


class FakeClusterAdapter:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.cached: dict[str, dict[str, Any]] = {}
        self.upsert_calls: list[list[dict[str, Any]]] = []
        self.replace_calls: list[list[dict[str, Any]]] = []
        self.release_calls: list[int] = []

    def try_advisory_lock(self, lock_id: int) -> bool:
        return True

    def release_advisory_lock(self, lock_id: int) -> None:
        self.release_calls.append(lock_id)

    def fetch_manual_pending_for_cluster(
        self,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return [dict(record) for record in self.records]

    def fetch_news_title_embeddings(
        self,
        article_ids: Sequence[str],
    ) -> list[dict[str, Any]]:
        return [
            dict(self.cached[article_id])
            for article_id in article_ids
            if article_id in self.cached
        ]

    def upsert_news_title_embeddings(
        self,
        embeddings: Sequence[Mapping[str, Any]],
    ) -> int:
        payload = [dict(item) for item in embeddings]
        self.upsert_calls.append(payload)
        for item in payload:
            self.cached[str(item["article_id"])] = dict(item)
        return len(payload)

    def replace_manual_clusters(
        self,
        clusters: Sequence[Mapping[str, Any]],
    ) -> int:
        payload = [dict(cluster) for cluster in clusters]
        self.replace_calls.append(payload)
        return len(payload)


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return [
        {
            "article_id": "article-1",
            "title": "相似标题一",
            "is_beijing_related": True,
            "sentiment_label": "positive",
            "external_importance_score": 100,
            "score": 90,
            "status": "pending",
        },
        {
            "article_id": "article-2",
            "title": "相似标题二",
            "is_beijing_related": True,
            "sentiment_label": "positive",
            "external_importance_score": 90,
            "score": 80,
            "status": "pending",
        },
        {
            "article_id": "article-3",
            "title": "不同标题",
            "is_beijing_related": True,
            "sentiment_label": "positive",
            "external_importance_score": 80,
            "score": 70,
            "status": "pending",
        },
    ]


def test_refresh_clusters_warms_cache_then_avoids_encoder(
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict[str, Any]],
) -> None:
    adapter = FakeClusterAdapter(records)
    encoded_batches: list[list[str]] = []

    def fake_encode(titles: Sequence[str]) -> np.ndarray:
        encoded_batches.append(list(titles))
        return np.asarray(
            [
                [1.0, 0.0],
                [0.99, 0.1],
                [0.0, 1.0],
            ],
            dtype=np.float32,
        )

    monkeypatch.setattr(manual_filter_cluster, "get_adapter", lambda: adapter)
    monkeypatch.setattr(manual_filter_cluster, "encode_texts", fake_encode)

    assert manual_filter_cluster.refresh_clusters() is True

    assert encoded_batches == [[
        "相似标题一",
        "相似标题二",
        "不同标题",
    ]]
    assert len(adapter.upsert_calls) == 1
    assert len(adapter.upsert_calls[0]) == 3
    assert all(
        item["model"] == EMBEDDING_MODEL_NAME
        for item in adapter.upsert_calls[0]
    )
    assert [
        cluster["item_ids"]
        for cluster in adapter.replace_calls[-1]
    ] == [
        ["article-1", "article-2"],
        ["article-3"],
    ]

    monkeypatch.setattr(
        manual_filter_cluster,
        "encode_texts",
        lambda titles: pytest.fail("hot cache must not invoke encoder"),
    )

    # Regression: refreshing the same global cluster IDs twice must not collide.
    assert manual_filter_cluster.refresh_clusters() is True
    assert len(adapter.upsert_calls) == 1
    assert len(adapter.replace_calls) == 2


def test_title_change_reencodes_only_changed_article(
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict[str, Any]],
) -> None:
    adapter = FakeClusterAdapter(records)
    monkeypatch.setattr(manual_filter_cluster, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        manual_filter_cluster,
        "encode_texts",
        lambda titles: np.eye(len(titles), dtype=np.float32),
    )
    assert manual_filter_cluster.refresh_clusters() is True

    records[0]["title"] = "已更新标题"
    encoded_batches: list[list[str]] = []

    def encode_changed(titles: Sequence[str]) -> np.ndarray:
        encoded_batches.append(list(titles))
        return np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32)

    monkeypatch.setattr(
        manual_filter_cluster,
        "encode_texts",
        encode_changed,
    )

    assert manual_filter_cluster.refresh_clusters() is True
    assert encoded_batches == [["已更新标题"]]
    assert [item["article_id"] for item in adapter.upsert_calls[-1]] == [
        "article-1",
    ]


def test_model_change_reencodes_only_mismatched_article(
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict[str, Any]],
) -> None:
    adapter = FakeClusterAdapter(records)
    monkeypatch.setattr(manual_filter_cluster, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        manual_filter_cluster,
        "encode_texts",
        lambda titles: np.eye(len(titles), dtype=np.float32),
    )
    assert manual_filter_cluster.refresh_clusters() is True

    adapter.cached["article-2"]["model"] = "old-model"
    encoded_batches: list[list[str]] = []

    def encode_mismatch(titles: Sequence[str]) -> np.ndarray:
        encoded_batches.append(list(titles))
        return np.asarray([[0.0, 1.0, 0.0]], dtype=np.float32)

    monkeypatch.setattr(
        manual_filter_cluster,
        "encode_texts",
        encode_mismatch,
    )

    assert manual_filter_cluster.refresh_clusters() is True
    assert encoded_batches == [["相似标题二"]]
    assert [item["article_id"] for item in adapter.upsert_calls[-1]] == [
        "article-2",
    ]
