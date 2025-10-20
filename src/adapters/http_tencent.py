from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse
import sys

import requests
from bs4 import BeautifulSoup
from bs4.element import Comment

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

AUTHOR_INFO_API = "https://i.news.qq.com/i/getUserHomepageInfo"
ARTICLE_LIST_API = "https://i.news.qq.com/getSubNewsMixedList"

DEFAULT_MAX_PAGES = 10
DEFAULT_DELAY_SECONDS = 0.5
DEFAULT_TAB_ID = "om_index"
DEFAULT_LOCALE = "zh-CN,zh;q=0.9"
REQUEST_RETRIES = 3
RETRY_BASE_DELAY = 0.75

CHINA_TZ = timezone(timedelta(hours=8))

DATA_BLOCK_PATTERN = re.compile(r"DATA\s*=\s*(\{.*?\});\s*</script>", re.S)

DEFAULT_AUTHORS_FILE = Path("config/qq_author.txt")


@dataclass(frozen=True)
class AuthorEntry:
    author_id: str
    profile_url: str
    raw_source: str
    tab_id: Optional[str] = None


@dataclass
class FeedItem:
    author_id: str
    profile_url: str
    article_id: str
    title: str
    url: str
    source: Optional[str]
    publish_time: Optional[int]
    publish_time_iso: Optional[str]
    summary: str
    raw: Dict[str, Any]


@dataclass
class ArticleDetail:
    author_id: str
    profile_url: str
    article_id: str
    title: str
    source: Optional[str]
    publish_time: Optional[int]
    publish_time_iso: Optional[str]
    url: str
    summary: str
    content_markdown: str


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": DEFAULT_LOCALE,
        }
    )
    return sess


def parse_author_id(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("Empty author value")
    if re.match(r"^https?://", value, re.I):
        parsed = urlparse(value)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            raise ValueError(f"Unable to derive author id from URL: {raw}")
        return segments[-1]
    return value


def canonical_profile_url(author_id: str) -> str:
    return f"https://news.qq.com/omn/author/{author_id}"


def load_author_entries(path: Path) -> List[AuthorEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Tencent author file not found: {path}")
    entries: List[AuthorEntry] = []
    seen: set[str] = set()
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        cleaned = line.strip().lstrip("\ufeff")
        if not cleaned or cleaned.startswith("#"):
            continue
        author_id = parse_author_id(cleaned)
        if author_id in seen:
            continue
        seen.add(author_id)
        profile_url = cleaned if cleaned.startswith("http") else canonical_profile_url(author_id)
        entries.append(
            AuthorEntry(
                author_id=author_id,
                profile_url=profile_url,
                raw_source=cleaned,
            )
        )
    return entries


def _request_with_retries(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
    retries: int = REQUEST_RETRIES,
) -> requests.Response:
    last_exc: Optional[Exception] = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            delay = RETRY_BASE_DELAY * (attempt + 1)
            time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("Unexpected empty response without exception")


def _safe_request_json(session: requests.Session, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    resp = _request_with_retries(session, url, params=params, timeout=10)
    data = resp.json()
    ret = data.get("ret")
    if ret not in (None, 0):
        msg = data.get("errmsg") or data.get("msg") or "unknown error"
        raise RuntimeError(f"Tencent API error {ret}: {msg}")
    return data


def fetch_author_profile(author_id: str, *, session: Optional[requests.Session] = None) -> Dict[str, Any]:
    sess = session or _session()
    params = {
        "guestSuid": author_id,
        "apptype": "web",
        "from_scene": "103",
        "isInGuest": "1",
    }
    data = _safe_request_json(sess, AUTHOR_INFO_API, params)
    userinfo = data.get("userinfo") or {}
    if not userinfo:
        raise RuntimeError(f"No author info returned for {author_id}")
    return userinfo


def resolve_tab_id(
    profile: Dict[str, Any],
    *,
    override: Optional[str] = None,
) -> str:
    if override:
        return override
    channel_cfg = profile.get("channel_config") or {}
    default_channel = channel_cfg.get("defaultChannelId")
    if default_channel:
        return str(default_channel)
    channel_list = channel_cfg.get("channel_list") or []
    for channel in channel_list:
        candidate = channel.get("channel_id")
        if candidate:
            return str(candidate)
    return DEFAULT_TAB_ID


def _parse_publish_time(value: Optional[str]) -> Tuple[Optional[int], Optional[str]]:
    if not value:
        return None, None
    candidate = value.strip()
    if not candidate:
        return None, None
    # Support common formats, including "2025-10-20 11:34:11"
    patterns = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y%m%d%H%M%S",
    ]
    for fmt in patterns:
        try:
            dt = datetime.strptime(candidate, fmt).replace(tzinfo=CHINA_TZ)
            return int(dt.timestamp()), dt.isoformat()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CHINA_TZ)
        else:
            dt = dt.astimezone(CHINA_TZ)
        return int(dt.timestamp()), dt.isoformat()
    except Exception:
        return None, None


def make_article_id(raw_id: str) -> str:
    cleaned = str(raw_id or "").strip()
    if not cleaned:
        raise ValueError("Empty Tencent article id")
    return f"tencent:{cleaned}"


def _summaries_from_payload(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    items = payload.get("newslist")
    if not isinstance(items, list):
        return []
    return items


def list_feed_items_for_author(
    entry: AuthorEntry,
    *,
    session: Optional[requests.Session] = None,
    tab_override: Optional[str] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> List[FeedItem]:
    sess = session or _session()
    profile = fetch_author_profile(entry.author_id, session=sess)
    tab_id = entry.tab_id or tab_override or resolve_tab_id(profile)
    author_name = (profile.get("name") or profile.get("nick") or "").strip() or None
    print(f"[info] Collecting Tencent feed for {entry.profile_url} (tab={tab_id})", file=sys.stderr)
    collected: List[FeedItem] = []
    offset = ""
    for page in range(max_pages):
        params = {
            "guestSuid": entry.author_id,
            "tabId": tab_id,
            "caller": "1",
            "from_scene": "103",
            "visit_type": "guest",
            "offset_info": offset,
        }
        payload = _safe_request_json(sess, ARTICLE_LIST_API, params)
        for item in _summaries_from_payload(payload):
            raw_article_id = item.get("id") or item.get("article_id")
            title = (item.get("title") or "").strip()
            url = (item.get("url") or item.get("surl") or "").strip()
            if not (raw_article_id and title and url):
                continue
            article_id = make_article_id(raw_article_id)
            publish_raw = item.get("time") or item.get("pubtime") or item.get("publish_time")
            publish_ts, publish_iso = _parse_publish_time(publish_raw)
            source_value = (item.get("source") or author_name or "").strip() or None
            summary_value = (item.get("abstract") or item.get("summary") or "").strip()
            collected.append(
                FeedItem(
                    author_id=entry.author_id,
                    profile_url=entry.profile_url,
                    article_id=article_id,
                    title=title,
                    url=url,
                    source=source_value,
                    publish_time=publish_ts,
                    publish_time_iso=publish_iso,
                    summary=summary_value,
                    raw=item,
                )
            )
        has_next = bool(payload.get("hasNext"))
        offset = payload.get("offsetInfo") or ""
        if not has_next or not offset:
            break
        if delay_seconds:
            time.sleep(delay_seconds)
    print(f"[info] Tencent author {entry.author_id} returned {len(collected)} items", file=sys.stderr)
    return collected


def list_feed_items(
    entries: Sequence[AuthorEntry],
    *,
    session: Optional[requests.Session] = None,
    tab_override: Optional[str] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    limit: Optional[int] = None,
) -> List[FeedItem]:
    sess = session or _session()
    aggregated: List[FeedItem] = []
    for entry in entries:
        if limit is not None and len(aggregated) >= limit:
            break
        items = list_feed_items_for_author(
            entry,
            session=sess,
            tab_override=tab_override,
            max_pages=max_pages,
            delay_seconds=delay_seconds,
        )
        if not items:
            continue
        aggregated.extend(items)
        if limit is not None and len(aggregated) >= limit:
            aggregated = aggregated[:limit]
            break
    return aggregated


def _extract_data_block(html_text: str) -> Dict[str, Any]:
    match = DATA_BLOCK_PATTERN.search(html_text)
    if not match:
        raise RuntimeError("Tencent DATA block not found in article page")
    raw_json = match.group(1)
    return json.loads(raw_json)


def _clean_html_to_markdown(html_fragment: str) -> str:
    if not html_fragment:
        return ""
    soup = BeautifulSoup(html_fragment, "html.parser")
    container = soup.find(class_="rich_media_content") or soup

    for element in container.find_all(["script", "style"]):
        element.decompose()

    for element in container.find_all(string=lambda text: isinstance(text, Comment)):
        element.extract()

    for br in container.find_all("br"):
        br.replace_with("\n")

    for img in container.find_all("img"):
        src = (img.get("data-src") or img.get("src") or "").strip()
        if not src:
            img.decompose()
            continue
        alt = (img.get("alt") or "").strip()
        replacement = f"\n\n![{alt}]({src})\n\n"
        img.replace_with(replacement)

    text = container.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines()]
    compact = [line for line in lines if line]
    markdown = "\n\n".join(compact).strip()
    return markdown


def fetch_article_detail(
    item: FeedItem,
    *,
    session: Optional[requests.Session] = None,
) -> ArticleDetail:
    sess = session or _session()
    resp = _request_with_retries(sess, item.url, timeout=10)
    data_block = _extract_data_block(resp.text)
    origin = data_block.get("originContent") or {}
    markdown = _clean_html_to_markdown(origin.get("text") or "")
    publish_raw = data_block.get("pubtime") or data_block.get("publish_time")
    publish_ts, publish_iso = _parse_publish_time(publish_raw)
    title = (data_block.get("title") or item.title or "").strip() or item.title
    source_value = (data_block.get("media_name") or data_block.get("source") or item.source or "").strip() or None
    detail_publish_time = publish_ts if publish_ts is not None else item.publish_time
    detail_publish_iso = publish_iso or item.publish_time_iso
    return ArticleDetail(
        author_id=item.author_id,
        profile_url=item.profile_url,
        article_id=item.article_id,
        title=title,
        source=source_value,
        publish_time=detail_publish_time,
        publish_time_iso=detail_publish_iso,
        url=item.url,
        summary=item.summary,
        content_markdown=markdown,
    )


def feed_item_to_row(item: FeedItem, *, fetched_at: datetime) -> Dict[str, Any]:
    return {
        "token": item.author_id,
        "profile_url": item.profile_url,
        "article_id": item.article_id,
        "title": item.title,
        "source": item.source,
        "publish_time": item.publish_time,
        "publish_time_iso": _parse_iso_datetime(item.publish_time_iso),
        "url": item.url,
        "summary": item.summary,
        "comment_count": 0,
        "digg_count": 0,
        "fetched_at": fetched_at,
    }


def build_detail_update(detail: ArticleDetail, *, detail_fetched_at: datetime) -> Dict[str, Any]:
    return {
        "token": detail.author_id,
        "profile_url": detail.profile_url,
        "title": detail.title,
        "source": detail.source,
        "publish_time": detail.publish_time,
        "publish_time_iso": _parse_iso_datetime(detail.publish_time_iso),
        "url": detail.url,
        "summary": detail.summary,
        "comment_count": 0,
        "digg_count": 0,
        "content_markdown": detail.content_markdown,
        "detail_fetched_at": detail_fetched_at,
        "article_id": detail.article_id,
    }


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CHINA_TZ)
        return dt
    except Exception:
        return None


__all__ = [
    "AuthorEntry",
    "FeedItem",
    "ArticleDetail",
    "DEFAULT_AUTHORS_FILE",
    "parse_author_id",
    "canonical_profile_url",
    "load_author_entries",
    "fetch_author_profile",
    "resolve_tab_id",
    "list_feed_items",
    "list_feed_items_for_author",
    "fetch_article_detail",
    "feed_item_to_row",
    "build_detail_update",
    "make_article_id",
]
