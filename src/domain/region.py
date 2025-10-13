"""
Helpers for determining whether articles are related to Beijing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Set


def load_beijing_keywords(path: Optional[Path]) -> Set[str]:
    """
    Load a set of Beijing-related keywords from a plaintext file.

    The file should contain one keyword per line. Returns an empty set if the
    path is None or the file does not exist.
    """
    if path is None:
        return set()
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()

    keywords: Set[str] = set()
    for raw_line in content.splitlines():
        token = raw_line.strip().lower()
        if token:
            keywords.add(token)
    return keywords


def is_beijing_related(texts: Iterable[str], keywords: Set[str]) -> bool:
    """
    Return True if any of the provided texts contain a Beijing keyword.

    Empty strings are ignored. Matching is case-insensitive and based on simple
    substring containment.
    """
    if not keywords:
        return False

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


__all__ = ["load_beijing_keywords", "is_beijing_related"]

