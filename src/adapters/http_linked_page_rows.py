from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Protocol


class LinkedPageFeedItem(Protocol):
    title: str
    url: str
    section: Optional[str]
    publish_time_iso: Optional[str]


def _dt_from_iso(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def feed_item_to_row(
    item: LinkedPageFeedItem,
    article_id: str,
    *,
    fetched_at: datetime,
) -> Dict[str, Any]:
    dt = _dt_from_iso(item.publish_time_iso)
    ts = int(dt.astimezone(timezone.utc).timestamp()) if dt else None
    return {
        'token': None,
        'profile_url': None,
        'article_id': article_id,
        'title': item.title,
        'source': item.section,
        'publish_time': ts,
        'publish_time_iso': dt,
        'url': item.url,
        'summary': None,
        'comment_count': None,
        'digg_count': None,
        'fetched_at': fetched_at,
    }


def build_detail_update(
    item: LinkedPageFeedItem,
    article_id: str,
    data: Dict[str, Any],
    *,
    detail_fetched_at: datetime,
    default_source: str,
    render_content: Callable[[str], str],
) -> Dict[str, Any]:
    pub_iso = data.get('publish_time_iso') or item.publish_time_iso
    pub_dt = _dt_from_iso(pub_iso)
    pub_ts = data.get('publish_time')
    if pub_ts is None and pub_dt is not None:
        pub_ts = int(pub_dt.astimezone(timezone.utc).timestamp())
    return {
        'token': None,
        'profile_url': None,
        'article_id': article_id,
        'title': (data.get('title') or item.title or '').strip(),
        'source': (data.get('source') or item.section or default_source).strip(),
        'publish_time': pub_ts,
        'publish_time_iso': pub_dt,
        'url': data.get('url') or item.url,
        'summary': None,
        'comment_count': None,
        'digg_count': None,
        'content_markdown': data.get('content_markdown') or render_content(data.get('content') or ''),
        'detail_fetched_at': detail_fetched_at,
    }


__all__ = ["LinkedPageFeedItem", "feed_item_to_row", "build_detail_update"]
