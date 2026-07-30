from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from src.adapters import title_cluster


def test_hf_hub_etag_timeout_default_is_20() -> None:
    assert title_cluster._DEFAULT_HF_HUB_ETAG_TIMEOUT == "20"


class _FakeSentenceTransformer:
    calls: list[tuple[str, bool]] = []
    fail_local = False

    def __init__(self, model_name: str, **kwargs: Any) -> None:
        local_files_only = bool(kwargs.get("local_files_only", False))
        self.calls.append((model_name, local_files_only))
        if local_files_only and self.fail_local:
            raise OSError("missing local model")


def test_load_model_prefers_local_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(title_cluster, "_model", None)
    monkeypatch.setattr(
        title_cluster,
        "_sentence_transformer_class",
        lambda: _FakeSentenceTransformer,
    )
    _FakeSentenceTransformer.calls = []
    _FakeSentenceTransformer.fail_local = False

    model = title_cluster._get_model()

    assert isinstance(model, _FakeSentenceTransformer)
    assert _FakeSentenceTransformer.calls == [("BAAI/bge-large-zh", True)]


def test_load_model_raises_when_local_cache_missing_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(title_cluster, "_model", None)
    monkeypatch.setattr(
        title_cluster,
        "_sentence_transformer_class",
        lambda: _FakeSentenceTransformer,
    )
    monkeypatch.delenv(title_cluster._MODEL_DOWNLOAD_ENV, raising=False)
    _FakeSentenceTransformer.calls = []
    _FakeSentenceTransformer.fail_local = True

    with pytest.raises(RuntimeError, match="explicitly allow downloading"):
        title_cluster._get_model()

    assert _FakeSentenceTransformer.calls == [("BAAI/bge-large-zh", True)]


def test_load_model_falls_back_online_only_when_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(title_cluster, "_model", None)
    monkeypatch.setattr(
        title_cluster,
        "_sentence_transformer_class",
        lambda: _FakeSentenceTransformer,
    )
    monkeypatch.setenv(title_cluster._MODEL_DOWNLOAD_ENV, "true")
    _FakeSentenceTransformer.calls = []
    _FakeSentenceTransformer.fail_local = True

    model = title_cluster._get_model()

    assert isinstance(model, _FakeSentenceTransformer)
    assert _FakeSentenceTransformer.calls == [
        ("BAAI/bge-large-zh", True),
        ("BAAI/bge-large-zh", False),
    ]


def test_cluster_embeddings_preserves_greedy_grouping_semantics() -> None:
    matrix = np.asarray(
        [
            [1.0, 0.0],
            [0.99, 0.1],
            [0.0, 1.0],
            [0.95, 0.2],
        ],
        dtype=np.float32,
    )

    groups = title_cluster.cluster_embeddings(matrix, threshold=0.9)

    assert groups == [[0, 1, 3], [2]]


def test_cluster_embeddings_keeps_zero_vectors_as_singletons() -> None:
    matrix = np.zeros((2, 3), dtype=np.float32)

    assert title_cluster.cluster_embeddings(matrix, threshold=0.9) == [
        [0],
        [1],
    ]


def test_cluster_titles_is_thin_embedding_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded: list[list[str]] = []

    def fake_encode(texts: list[str]) -> np.ndarray:
        encoded.append(texts)
        return np.asarray([[1.0, 0.0], [0.99, 0.1]], dtype=np.float32)

    monkeypatch.setattr(title_cluster, "encode_texts", fake_encode)

    groups = title_cluster.cluster_titles(
        ["标题一", "标题二"],
        threshold=0.9,
    )

    assert encoded == [["标题一", "标题二"]]
    assert groups == [[0, 1]]


def test_cluster_titles_keeps_single_title_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        title_cluster,
        "encode_texts",
        lambda titles: pytest.fail("single title must not invoke encoder"),
    )

    assert title_cluster.cluster_titles(["唯一标题"]) == [[0]]
