from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Dict, List, Optional

from src.adapters.db_postgres_core import get_adapter


DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS = 30
MAX_ARTICLE_SEARCH_LOOKBACK_DAYS = 3650


def _get_adapter_safe():
    try:
        return get_adapter()
    except Exception:  # pragma: no cover - degrade gracefully when DB is unavailable
        return None


def _to_list(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    return []


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _serialize_article(row: Dict[str, Any]) -> Dict[str, Any]:
    llm_keywords = _to_list(row.get("llm_keywords"))
    keywords = _to_list(row.get("keywords")) or llm_keywords
    return {
        "article_id": str(row.get("article_id") or ""),
        "title": row.get("title"),
        "source": row.get("source"),
        "publish_time": _to_int(row.get("publish_time")),
        "publish_time_iso": row.get("publish_time_iso"),
        "url": row.get("url"),
        "llm_summary": row.get("llm_summary"),
        "keywords": keywords,
        "llm_keywords": llm_keywords,
        "score": _to_float(row.get("score")),
        "raw_relevance_score": _to_float(row.get("raw_relevance_score")),
        "keyword_bonus_score": _to_float(row.get("keyword_bonus_score")),
        "sentiment_label": row.get("sentiment_label"),
        "sentiment_confidence": _to_float(row.get("sentiment_confidence")),
        "status": row.get("status"),
        "summary_status": row.get("summary_status"),
        "external_importance_status": row.get("external_importance_status"),
        "external_importance_score": _to_float(row.get("external_importance_score")),
        "is_beijing_related": row.get("is_beijing_related"),
        "is_beijing_related_llm": row.get("is_beijing_related_llm"),
        "external_importance_checked_at": row.get("external_importance_checked_at"),
        "summary_generated_at": row.get("summary_generated_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "attribution": {
            "level": row.get("attribution_level"),
            "is_fallback": bool(row.get("attribution_is_fallback")),
            "ingested_at": row.get("attribution_ingested_at"),
            "ingested_at_source": row.get("attribution_ingested_at_source"),
            "relevance_score": _to_float(row.get("attribution_relevance_score")),
            "importance_score": _to_float(row.get("attribution_importance_score")),
            "manual_decisions": list(row.get("attribution_manual_decisions") or []),
            "export_batch_dates": list(row.get("attribution_export_batch_dates") or []),
            "matched_article_title": row.get("attribution_matched_article_title"),
        },
    }


def search_articles(
    *,
    query: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    lookback_days: int = DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS,
) -> Dict[str, Any]:
    adapter = _get_adapter_safe()
    limit = max(1, min(int(limit or 20), 100))
    page = max(1, int(page or 1))
    lookback_days = max(
        1,
        min(
            int(lookback_days or DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS),
            MAX_ARTICLE_SEARCH_LOOKBACK_DAYS,
        ),
    )
    window_start = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    offset = (page - 1) * limit
    if adapter is None:
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "page": page,
            "pages": 1,
            "lookback_days": lookback_days,
            "window_start": window_start,
        }
    raw = adapter.news_summaries.search_with_attribution(
        query=query,
        fetched_after=window_start,
        limit=limit,
        offset=offset,
    )
    items = [_serialize_article(row) for row in raw.get("items", [])]
    total = int(raw.get("total") or 0)
    pages = max(1, ceil(total / limit)) if limit else 1
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "page": page,
        "pages": pages,
        "lookback_days": lookback_days,
        "window_start": window_start,
    }


def get_article_content(*, article_id: str) -> Dict[str, Any]:
    adapter = _get_adapter_safe()
    safe_article_id = str(article_id or "")
    unavailable = {
        "article_id": safe_article_id,
        "title": None,
        "source": None,
        "url": None,
        "created_at": None,
        "content_markdown": None,
    }
    if adapter is None:
        return unavailable
    try:
        row = adapter.news_summaries.fetch_content(safe_article_id)
    except Exception:  # pragma: no cover - degrade gracefully when DB is unavailable
        return unavailable
    if not row:
        return unavailable
    return {
        "article_id": str(row.get("article_id") or safe_article_id),
        "title": row.get("title"),
        "source": row.get("source"),
        "url": row.get("url"),
        "created_at": row.get("created_at"),
        "content_markdown": row.get("content_markdown"),
    }


__all__ = [
    "DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS",
    "MAX_ARTICLE_SEARCH_LOOKBACK_DAYS",
    "get_article_content",
    "search_articles",
]
