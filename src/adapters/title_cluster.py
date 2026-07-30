from __future__ import annotations

"""Reusable title clustering utility based on BGE embeddings."""

import os
import struct
import threading
from typing import Any, Sequence

import numpy as np

_DEFAULT_HF_HUB_ETAG_TIMEOUT = "20"
_MODEL_DOWNLOAD_ENV = "TITLE_CLUSTER_ALLOW_MODEL_DOWNLOAD"
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", _DEFAULT_HF_HUB_ETAG_TIMEOUT)

from src.config import BGE_EMBEDDING_MODEL

EMBEDDING_MODEL_NAME = BGE_EMBEDDING_MODEL
_DEFAULT_MODEL_NAME = EMBEDDING_MODEL_NAME
_DEFAULT_THRESHOLD = 0.9

_model: Any = None
_model_lock = threading.Lock()


def _model_download_allowed() -> bool:
    value = os.getenv(_MODEL_DOWNLOAD_ENV, "")
    return value.strip().lower() in _TRUE_VALUES


def _sentence_transformer_class() -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer


def _load_model() -> Any:
    sentence_transformer = _sentence_transformer_class()
    try:
        return sentence_transformer(
            _DEFAULT_MODEL_NAME,
            local_files_only=True,
        )
    except OSError as exc:
        if not _model_download_allowed():
            raise RuntimeError(
                f"Embedding model {_DEFAULT_MODEL_NAME!r} is unavailable locally. "
                f"Set {_MODEL_DOWNLOAD_ENV}=1 to explicitly allow downloading it."
            ) from exc
        return sentence_transformer(_DEFAULT_MODEL_NAME)


def _get_model() -> Any:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = _load_model()
    return _model


def get_embedding_model() -> Any:
    """Return the process-wide BGE model singleton."""
    return _get_model()


def encode_texts(texts: Sequence[str]) -> Any:
    """Encode text as normalized NumPy vectors using the shared BGE model."""
    values = [text or "" for text in texts]
    if not values:
        return []
    return get_embedding_model().encode(
        values,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )


def pack_embedding(vector: Sequence[float]) -> bytes:
    values = [float(value) for value in vector]
    return struct.pack(f"<{len(values)}f", *values)


def unpack_embedding(value: bytes) -> np.ndarray:
    return np.frombuffer(value, dtype="<f4")


def _greedy_grouping(
    sim_matrix: np.ndarray,
    threshold: float,
) -> list[list[int]]:
    unassigned = np.ones(len(sim_matrix), dtype=bool)
    groups: list[list[int]] = []
    for i in range(len(sim_matrix)):
        if not unassigned[i]:
            continue
        matches = unassigned & (sim_matrix[i] >= threshold)
        matches[:i + 1] = False
        group = [i, *np.flatnonzero(matches).tolist()]
        unassigned[group] = False
        groups.append(group)
    return groups


def cluster_embeddings(
    matrix: Sequence[Sequence[float]] | np.ndarray,
    *,
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[list[int]]:
    values = np.asarray(matrix, dtype=np.float32)
    if values.size == 0:
        return []
    if values.ndim != 2:
        raise ValueError("embedding matrix must be two-dimensional")
    if len(values) == 1:
        return [[0]]
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    normalized = np.divide(
        values,
        norms,
        out=np.zeros_like(values),
        where=norms > 0,
    )
    similarities = normalized @ normalized.T
    return _greedy_grouping(similarities, threshold)


def cluster_titles(
    titles: Sequence[str],
    *,
    threshold: float = _DEFAULT_THRESHOLD,
) -> list[list[int]]:
    """
    Cluster titles using cosine similarity on BGE embeddings.

    Args:
        titles: Sequence of news titles.
        threshold: Similarity threshold within [0, 1].

    Returns:
        List of clusters, each cluster is a list of original indices.
        Empty input yields an empty list.
    """
    titles_list = [title or "" for title in titles]
    if not titles_list:
        return []
    if len(titles_list) == 1:
        return [[0]]
    return cluster_embeddings(
        encode_texts(titles_list),
        threshold=threshold,
    )


__all__ = [
    "EMBEDDING_MODEL_NAME",
    "cluster_embeddings",
    "cluster_titles",
    "encode_texts",
    "get_embedding_model",
    "pack_embedding",
    "unpack_embedding",
]
