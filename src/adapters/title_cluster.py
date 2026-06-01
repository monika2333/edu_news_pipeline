from __future__ import annotations

"""Reusable title clustering utility based on BGE embeddings."""

import threading
from typing import Sequence

from sentence_transformers import SentenceTransformer, util

_DEFAULT_MODEL_NAME = "BAAI/bge-large-zh"
_DEFAULT_THRESHOLD = 0.9

_model: SentenceTransformer | None = None
_model_lock = threading.Lock()


def _load_model() -> SentenceTransformer:
    try:
        return SentenceTransformer(_DEFAULT_MODEL_NAME, local_files_only=True)
    except OSError:
        return SentenceTransformer(_DEFAULT_MODEL_NAME)


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = _load_model()
    return _model


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


__all__ = ["cluster_titles"]
