from __future__ import annotations

from datetime import date
from math import ceil
from typing import Any, Dict, List, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter


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
        "content_markdown": row.get("content_markdown"),
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
    }


def _normalize(values: Optional[Sequence[str]]) -> Optional[List[str]]:
    if not values:
        return None
    cleaned = [item.strip() for item in values if item and item.strip()]
    return cleaned or None


def search_articles(
    *,
    query: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
    sources: Optional[Sequence[str]] = None,
    sentiments: Optional[Sequence[str]] = None,
    statuses: Optional[Sequence[str]] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    adapter = _get_adapter_safe()
    limit = max(1, min(int(limit or 20), 100))
    page = max(1, int(page or 1))
    offset = (page - 1) * limit
    if adapter is None:
        return {
            "items": [],
            "total": 0,
            "limit": limit,
            "page": page,
            "pages": 1,
        }
    raw = adapter.search_news_summaries(
        query=query,
        sources=_normalize(sources),
        sentiments=_normalize(sentiments),
        statuses=_normalize(statuses),
        start_date=start_date,
        end_date=end_date,
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
    }


__all__ = ["search_articles"]
