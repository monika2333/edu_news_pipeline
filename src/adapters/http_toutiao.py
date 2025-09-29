from __future__ import annotations

from typing import Any, Dict, List


def fetch_author_feed(author_id: str, limit: int) -> List[Dict[str, Any]]:
    """Pull a trimmed Toutiao feed for the given author.

    The old `tools/toutiao_scraper.py` logic should move here. Return a list of
    dictionaries ready to be normalised by the worker.
    """
    raise NotImplementedError("fetch_author_feed needs an implementation")


def fetch_article_content(article_url: str, timeout: int = 15) -> Dict[str, Any]:
    """Fetch article details and clean the payload for storage."""
    raise NotImplementedError("fetch_article_content needs an implementation")


__all__ = ["fetch_author_feed", "fetch_article_content"]
