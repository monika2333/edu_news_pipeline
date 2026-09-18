"""
Helpers for determining whether articles are related to Beijing.
"""

from __future__ import annotations

from typing import Iterable


def is_beijing_related(texts: Iterable[str], keywords: Iterable[str]) -> bool:
    """
    Return True if any of the provided texts contain a Beijing keyword.

    Empty strings are ignored. Matching is case-insensitive and based on simple
    substring containment. Keywords come from the frozen business config
    (``beijing_keywords`` section), which validation guarantees to be non-empty.
    """
    lowered_keywords = tuple(k.lower() for k in keywords if k)
    if not lowered_keywords:
        return False

    for text in texts:
        if not text:
            continue
        normalized = str(text).lower()
        if any(keyword in normalized for keyword in lowered_keywords):
            return True
    return False


__all__ = ["is_beijing_related"]
