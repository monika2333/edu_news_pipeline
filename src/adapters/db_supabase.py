from __future__ import annotations

from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from src.config import get_settings

_client: Optional[Client] = None


def get_client() -> Client:
    """Return a shared Supabase client instance configured from settings."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


def fetch_articles_for_summary(limit: int) -> List[Dict[str, Any]]:
    """Fetch a batch of articles waiting for summarisation.

    Replace this stub with the concrete query you were previously running via
    the `tools/summarize_supabase.py` script.
    """
    raise NotImplementedError("fetch_articles_for_summary needs an implementation")


def upsert_summary(article_id: str, summary: str) -> None:
    """Write the generated summary back to Supabase."""
    raise NotImplementedError("upsert_summary needs an implementation")


def record_export(entries: List[Dict[str, Any]]) -> None:
    """Persist export metadata for traceability."""
    raise NotImplementedError("record_export needs an implementation")


__all__ = [
    "get_client",
    "fetch_articles_for_summary",
    "upsert_summary",
    "record_export",
]
