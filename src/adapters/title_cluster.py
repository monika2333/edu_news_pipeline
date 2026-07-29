from __future__ import annotations

"""Reusable title clustering utility based on BGE embeddings."""

import os
import threading
from typing import Sequence

_DEFAULT_HF_HUB_ETAG_TIMEOUT = "20"
_MODEL_DOWNLOAD_ENV = "TITLE_CLUSTER_ALLOW_MODEL_DOWNLOAD"
_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", _DEFAULT_HF_HUB_ETAG_TIMEOUT)

from sentence_transformers import SentenceTransformer, util

from src.config import BGE_EMBEDDING_MODEL

EMBEDDING_MODEL_NAME = BGE_EMBEDDING_MODEL
_DEFAULT_MODEL_NAME = EMBEDDING_MODEL_NAME
_DEFAULT_THRESHOLD = 0.9

_model: SentenceTransformer | None = None
_model_lock = threading.Lock()


def _model_download_allowed() -> bool:
    value = os.getenv(_MODEL_DOWNLOAD_ENV, "")
    return value.strip().lower() in _TRUE_VALUES


def _load_model() -> SentenceTransformer:
    try:
        return SentenceTransformer(_DEFAULT_MODEL_NAME, local_files_only=True)
    except OSError as exc:
        if not _model_download_allowed():
            raise RuntimeError(
                f"Embedding model {_DEFAULT_MODEL_NAME!r} is unavailable locally. "
                f"Set {_MODEL_DOWNLOAD_ENV}=1 to explicitly allow downloading it."
            ) from exc
        return SentenceTransformer(_DEFAULT_MODEL_NAME)


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = _load_model()
    return _model


def get_embedding_model() -> SentenceTransformer:
    """Return the process-wide BGE model singleton."""
    return _get_model()


def encode_texts(texts: Sequence[str]):
    """Encode text as normalized NumPy vectors using the shared BGE model."""
    values = [text or "" for text in texts]
    if not values:
        return []
    return get_embedding_model().encode(
        values,
        convert_to_numpy=True,
        normalize_embeddings=True,
    )


def _greedy_grouping(sim_matrix, threshold: float) -> list[list[int]]:
    visited = set()
    groups: list[list[int]] = []
    size = len(sim_matrix)
    for i in range(size):
        if i in visited:
            continue
        group = [i]
        visited.add(i)
        for j in range(i + 1, size):
            if j not in visited and sim_matrix[i][j] >= threshold:
                group.append(j)
                visited.add(j)
        groups.append(group)
    return groups


def cluster_titles(titles: Sequence[str], *, threshold: float = _DEFAULT_THRESHOLD) -> list[list[int]]:
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

    model = _get_model()
    embeddings = model.encode(titles_list, convert_to_tensor=True, normalize_embeddings=True)
    sim_matrix = util.cos_sim(embeddings, embeddings).cpu().numpy()

    return _greedy_grouping(sim_matrix, threshold)


__all__ = [
    "EMBEDDING_MODEL_NAME",
    "cluster_titles",
    "encode_texts",
    "get_embedding_model",
]
