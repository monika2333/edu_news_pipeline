from __future__ import annotations

from math import ceil
from typing import Any, Dict, List, Optional

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


def search_articles(
    *,
    query: Optional[str] = None,
    page: int = 1,
    limit: int = 20,
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


def get_article_content(*, article_id: str) -> Dict[str, Any]:
    adapter = _get_adapter_safe()
    safe_article_id = str(article_id or "")
    if adapter is None:
        return {"article_id": safe_article_id, "content_markdown": None}
    row = adapter.fetch_news_summary_content(safe_article_id)
    if not row:
        return {"article_id": safe_article_id, "content_markdown": None}
    return {
        "article_id": str(row.get("article_id") or safe_article_id),
        "content_markdown": row.get("content_markdown"),
    }


__all__ = ["search_articles", "get_article_content"]
