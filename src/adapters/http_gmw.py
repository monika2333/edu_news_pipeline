from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from gmw_crawl.gmw_crawler import Article as _LegacyArticle
from gmw_crawl.gmw_crawler import DEFAULT_LISTING_URL as GMW_DEFAULT_LISTING_URL
from gmw_crawl.gmw_crawler import GMWCrawler as _LegacyCrawler

CHINA_TZ = timezone(timedelta(hours=8))
SOURCE_NAME = "Guangming Daily"
DEFAULT_TIMEOUT = 15.0
DEFAULT_BASE_URL = GMW_DEFAULT_LISTING_URL


@dataclass
class GMWArticle:
    title: str
    url: str
    publish_time: Optional[int]
    publish_time_iso: Optional[datetime]
    content_markdown: str
    raw_publish_text: Optional[str]


def fetch_articles(
    limit: Optional[int] = None,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[GMWArticle]:
    """Return Guangming Daily articles using the standalone crawler implementation."""
    crawler = _LegacyCrawler(base_url=base_url, timeout=timeout)
    raw_articles = crawler.crawl(max_articles=limit)
    parsed: List[GMWArticle] = []
    for raw in raw_articles:
        publish_ts, publish_dt = _parse_publish_time(raw.publish_time)
        parsed.append(
            GMWArticle(
                title=(raw.title or "").strip(),
                url=raw.url,
                publish_time=publish_ts,
                publish_time_iso=publish_dt,
                content_markdown=_normalize_markdown(raw.content_markdown),
                raw_publish_text=raw.publish_time,
            )
        )
    return parsed


def make_article_id(url: str) -> str:
    """Derive a stable article identifier from the Guangming Daily URL."""
    parsed = urlparse((url or "").strip())
    path = parsed.path or "/"
    path = re.sub(r"\.s?htm[l]?$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/+", "/", path).strip("/")
    if not path:
        path = "index"
    return f"gmw:{path}"


def article_to_feed_row(article: GMWArticle, article_id: str, *, fetched_at: datetime) -> Dict[str, Any]:
    return {
        "token": None,
        "profile_url": None,
        "article_id": article_id,
        "title": article.title,
        "source": SOURCE_NAME,
        "publish_time": article.publish_time,
        "publish_time_iso": article.publish_time_iso,
        "url": article.url,
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "fetched_at": fetched_at,
    }


def article_to_detail_row(
    article: GMWArticle,
    article_id: str,
    *,
    detail_fetched_at: datetime,
) -> Dict[str, Any]:
    return {
        "token": None,
        "profile_url": None,
        "article_id": article_id,
        "title": article.title,
        "source": SOURCE_NAME,
        "publish_time": article.publish_time,
        "publish_time_iso": article.publish_time_iso,
        "url": article.url,
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "content_markdown": article.content_markdown,
        "detail_fetched_at": detail_fetched_at,
    }


def _normalize_markdown(value: str) -> str:
    text = (value or "").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _parse_publish_time(value: Optional[str]) -> Tuple[Optional[int], Optional[datetime]]:
    if not value:
        return None, None
    cleaned = _normalize_publish_text(value)
    if not cleaned:
        return None, None
    dt = _coerce_datetime(cleaned)
    if dt is None:
        return None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHINA_TZ)
    publish_ts = int(dt.timestamp())
    return publish_ts, dt


def _normalize_publish_text(value: str) -> str:
    text = value.strip()
    replacements = {
        "年": "-",
        "月": "-",
        "日": " ",
        "时": ":",
        "点": ":",
        "分": ":",
        "秒": "",
        "/": "-",
        "．": ".",
        "。": ".",
    }
    for needle, repl in replacements.items():
        text = text.replace(needle, repl)
    text = re.sub(r"[．。]", ".", text)
    text = re.sub(r"[-]{2,}", "-", text)
    text = re.sub(r":{2,}", ":", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:")


def _coerce_datetime(value: str) -> Optional[datetime]:
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H",
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
    )
    for pattern in candidates:
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    match = re.search(
        r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})(?:\s+(\d{1,2})(?::(\d{1,2})(?::(\d{1,2}))?)?)?",
        value,
    )
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    hour = hour or "0"
    minute = minute or "0"
    second = second or "0"
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
        )
    except ValueError:
        return None


__all__ = [
    "GMWArticle",
    "fetch_articles",
    "make_article_id",
    "article_to_feed_row",
    "article_to_detail_row",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "SOURCE_NAME",
]
