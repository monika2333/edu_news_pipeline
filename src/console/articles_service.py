from __future__ import annotations

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Dict, List, Optional

from src.adapters.db_postgres_core import get_adapter


DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS = 30
MAX_ARTICLE_SEARCH_LOOKBACK_DAYS = 3650
ARTICLE_SEARCH_CURSOR_VERSION = 1


def _query_digest(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _encode_search_cursor(
    *,
    ingested_at: datetime,
    article_id: str,
    window_start: datetime,
    query: str,
    lookback_days: int,
) -> str:
    payload = json.dumps(
        {
            "v": ARTICLE_SEARCH_CURSOR_VERSION,
            "t": ingested_at.isoformat(),
            "id": article_id,
            "ws": window_start.isoformat(),
            "qh": _query_digest(query),
            "d": lookback_days,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_search_cursor(
    cursor: Optional[str],
    *,
    query: str,
    lookback_days: int,
) -> tuple[Optional[datetime], Optional[str], Optional[datetime]]:
    normalized = str(cursor or "").strip()
    if not normalized:
        return None, None, None
    try:
        padding = "=" * (-len(normalized) % 4)
        payload = json.loads(base64.urlsafe_b64decode(normalized + padding).decode("utf-8"))
        if payload.get("v") != ARTICLE_SEARCH_CURSOR_VERSION:
            raise ValueError("Unsupported article search cursor version")
        ingested_at = datetime.fromisoformat(str(payload["t"]))
        article_id = str(payload["id"])
        window_start = datetime.fromisoformat(str(payload["ws"]))
    except (binascii.Error, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid article search cursor") from exc
    if ingested_at.tzinfo is None or window_start.tzinfo is None or not article_id:
        raise ValueError("Invalid article search cursor")
    if payload.get("qh") != _query_digest(query) or payload.get("d") != lookback_days:
        raise ValueError("Article search cursor does not match the current search")
    return ingested_at, article_id, window_start


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
            "ingested_at": row.get("attribution_ingested_at"),
            "ingested_at_source": row.get("attribution_ingested_at_source"),
            "relevance_score": _to_float(row.get("attribution_relevance_score")),
            "importance_score": _to_float(row.get("attribution_importance_score")),
            "manual_decisions": list(row.get("attribution_manual_decisions") or []),
            "matched_article_title": row.get("attribution_matched_article_title"),
        },
        "archive_links": list(row.get("archive_links") or []),
        # 仅在有反馈记录时给出 dict，前端据此渲染 ▲/▼；无记录为 None。
        "score_feedback": (
            {
                "feedback_type": row["score_feedback_type"],
                "notes": row.get("score_feedback_notes"),
            }
            if row.get("score_feedback_type") in ("too_high", "too_low")
            else None
        ),
    }


def search_articles(
    *,
    query: str,
    limit: int = 20,
    lookback_days: int = DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise ValueError("Article search query must not be blank")
    limit = max(1, min(int(limit or 20), 100))
    lookback_days = max(
        1,
        min(
            int(lookback_days or DEFAULT_ARTICLE_SEARCH_LOOKBACK_DAYS),
            MAX_ARTICLE_SEARCH_LOOKBACK_DAYS,
        ),
    )
    cursor_ingested_at, cursor_article_id, cursor_window_start = _decode_search_cursor(
        cursor,
        query=normalized_query,
        lookback_days=lookback_days,
    )
    window_start = cursor_window_start or (
        datetime.now(timezone.utc) - timedelta(days=lookback_days)
    )
    adapter = _get_adapter_safe()
    if adapter is None:
        return {
            "items": [],
            "limit": limit,
            "has_more": False,
            "next_cursor": None,
            "lookback_days": lookback_days,
            "window_start": window_start,
        }
    raw = adapter.news_summaries.search_with_attribution(
        query=normalized_query,
        fetched_after=window_start,
        limit=limit,
        cursor_ingested_at=cursor_ingested_at,
        cursor_article_id=cursor_article_id,
    )
    items = [_serialize_article(row) for row in raw.get("items", [])]
    has_more = bool(raw.get("has_more"))
    next_cursor = None
    next_ingested_at = raw.get("next_ingested_at")
    next_article_id = raw.get("next_article_id")
    if has_more and isinstance(next_ingested_at, datetime) and next_article_id:
        next_cursor = _encode_search_cursor(
            ingested_at=next_ingested_at,
            article_id=str(next_article_id),
            window_start=window_start,
            query=normalized_query,
            lookback_days=lookback_days,
        )
    return {
        "items": items,
        "limit": limit,
        "has_more": has_more,
        "next_cursor": next_cursor,
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
