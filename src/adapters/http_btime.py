from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from src.adapters.http_linked_page_rows import build_detail_update as build_linked_detail_update
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

LIST_API_URL = "https://record.btime.com/getNews"
PROFILE_BASE_URL = "https://record.btime.com/show"
ITEM_BASE_URL = "https://item.btime.com/"
SOURCE_NAME = "北京时间"
ARTICLE_ID_PREFIX = "btime:"
DEFAULT_UIDS_FILE = Path("config/btime_uid.txt")
UIDS_PATH_ENV = "BTIME_UIDS_PATH"
CHINA_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


@dataclass(frozen=True)
class UidEntry:
    uid: str
    profile_url: str
    raw_source: str


@dataclass
class FeedItemLike:
    title: str
    url: str
    section: Optional[str]
    publish_time_iso: Optional[str]
    raw: dict[str, Any]
    gid: str


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": USER_AGENT,
        }
    )
    return session


def _response_text(response: requests.Response) -> str:
    encoding = (response.encoding or "").lower()
    if not encoding or encoding == "iso-8859-1":
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text or ""


def profile_url(uid: str) -> str:
    return f"{PROFILE_BASE_URL}?uid={uid}"


def parse_uid(raw: str) -> str:
    value = (raw or "").strip().lstrip("\ufeff")
    if not value:
        raise ValueError("Empty Btime uid")
    if re.match(r"^https?://", value, re.IGNORECASE):
        query = parse_qs(urlsplit(value).query)
        value = str((query.get("uid") or [""])[0]).strip()
    if not re.fullmatch(r"\d+", value):
        raise ValueError(f"Unable to derive Btime uid from: {raw}")
    return value


def load_uid_entries(path: Path) -> list[UidEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Btime uid file not found: {path}")
    entries: list[UidEntry] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().lstrip("\ufeff")
        if not cleaned or cleaned.startswith("#"):
            continue
        uid = parse_uid(cleaned)
        if uid in seen:
            continue
        seen.add(uid)
        entries.append(
            UidEntry(
                uid=uid,
                profile_url=cleaned if re.match(r"^https?://", cleaned, re.IGNORECASE) else profile_url(uid),
                raw_source=cleaned,
            )
        )
    return entries


def _resolve_uids_path() -> Path:
    configured = os.getenv(UIDS_PATH_ENV)
    return Path(configured.strip()) if configured and configured.strip() else DEFAULT_UIDS_FILE


def _build_list_params(uid: str) -> dict[str, Any]:
    return {
        "tab": "all",
        "pageRow": 10,
        "uid": uid,
        # This must stay 1: refresh=0 silently returns the account's 2020 archive.
        "refresh": 1,
        "target": "v4",
        "offset": 0,
        "refresh_type": 1,
        "req_count": 1,
        "page": 1,
    }


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    resolved = urljoin(ITEM_BASE_URL, raw)
    parsed = urlsplit(resolved)
    scheme = "https" if (parsed.hostname or "").lower().endswith("btime.com") else parsed.scheme.lower()
    return urlunsplit((scheme, parsed.netloc.lower(), parsed.path, "", ""))


def make_article_id(value: str) -> str:
    cleaned = (value or "").strip()
    if re.match(r"^https?://|^//", cleaned, re.IGNORECASE):
        cleaned = urlsplit(normalize_url(cleaned)).path.rstrip("/").rsplit("/", 1)[-1]
    cleaned = cleaned.strip().strip("/")
    if not cleaned:
        raise ValueError("Empty Btime article id")
    return f"{ARTICLE_ID_PREFIX}{cleaned}"


def _publish_time_iso(value: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(value), tz=CHINA_TZ).isoformat()
    except (OSError, TypeError, ValueError):
        return None


def _parse_feed_payload(
    payload: dict[str, Any],
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    code = payload.get("code")
    if code not in (None, 0):
        raise RuntimeError(f"Btime list API error {code}: {payload.get('message') or 'unknown error'}")
    container = payload.get("data") or {}
    rows = container.get("data") if isinstance(container, dict) else None
    if not isinstance(rows, list):
        return []

    items: list[FeedItemLike] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        data = row.get("data") or {}
        if not isinstance(data, dict):
            continue
        gid = str(row.get("gid") or "").strip()
        title = str(data.get("title") or "").strip()
        url = normalize_url(str(row.get("open_url") or row.get("url") or ""))
        if not (gid and title and url):
            continue
        article_id = make_article_id(gid)
        if article_id in seen or (existing_ids is not None and article_id in existing_ids):
            continue
        seen.add(article_id)
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=str(data.get("source") or SOURCE_NAME).strip(),
                publish_time_iso=_publish_time_iso(data.get("pdate")),
                raw=row,
                gid=gid,
            )
        )
    return items


def list_items(
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    del pages  # Next-page parameter changes are unconfirmed, so only the verified first packet is fetched.
    if limit is not None and limit <= 0:
        return []

    entries = load_uid_entries(_resolve_uids_path())
    session = _session()
    items: list[FeedItemLike] = []
    known_ids = set(existing_ids or set())
    for entry in entries:
        response = session.get(
            LIST_API_URL,
            params=_build_list_params(entry.uid),
            headers={"Referer": entry.profile_url},
            timeout=15,
        )
        response.raise_for_status()
        batch = _parse_feed_payload(response.json(), existing_ids=known_ids)
        for item in batch:
            items.append(item)
            known_ids.add(make_article_id(item.gid))
            if limit is not None and len(items) >= limit:
                return items
    return items


def html_to_markdown(html_str: str) -> str:
    soup = BeautifulSoup(html_str or "", "html.parser")
    for unwanted in soup.select("script, style, noscript, iframe"):
        unwanted.decompose()
    for image in soup.find_all("img"):
        src = str(image.get("src") or image.get("data-src") or "").strip()
        alt = str(image.get("alt") or "").strip()
        image.replace_with(f"\n\n![{alt}]({src})\n\n" if src else "")
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for block in soup.find_all(["p", "div", "figure", "h1", "h2", "h3", "li", "blockquote"]):
        block.insert_before("\n\n")
        block.insert_after("\n\n")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in soup.get_text().splitlines()]
    return "\n\n".join(line for line in lines if line)


def _clean_content_node(content_node: Tag) -> Tag:
    clone = BeautifulSoup(str(content_node), "html.parser")
    cleaned = clone.find()
    if not isinstance(cleaned, Tag):
        return content_node
    noise_selectors = (
        "script, style, noscript, iframe, .share, .share-box, .recommend, .related, "
        ".advertisement, .ad, .comment, .comments, .toolbar, .editor, .copyright"
    )
    for unwanted in cleaned.select(noise_selectors):
        unwanted.decompose()
    return cleaned


def _strip_title_suffix(title: str) -> str:
    return re.sub(r"\s*[_|\-]\s*北京时间\s*$", "", title or "").strip()


def _parse_detail_html(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    title_node = soup.select_one(".article_content > h1, h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)

    content_node = None
    for selector in (
        ".seo_aritcle_content .article_content > article",
        ".article_content > article",
        ".article_content > .aritcle.content",
        "article",
    ):
        content_node = soup.select_one(selector)
        if content_node is not None:
            break
    content_html = ""
    content_markdown = ""
    if isinstance(content_node, Tag):
        cleaned = _clean_content_node(content_node)
        content_html = cleaned.decode_contents()
        content_markdown = html_to_markdown(content_html)

    # Short or empty bodies are valid for video posts and must not be reported as crawl failures.
    return {
        "title": _strip_title_suffix(title) or None,
        "source": None,
        "publish_time": None,
        "publish_time_iso": None,
        "url": normalize_url(url),
        "content": content_html,
        "content_markdown": content_markdown,
    }


def fetch_detail(url: str) -> dict[str, Any]:
    session = _session()
    response = session.get(normalize_url(url), timeout=15)
    response.raise_for_status()
    return _parse_detail_html(_response_text(response), url)


def feed_item_to_row(
    item: FeedItemLike,
    article_id: str,
    *,
    fetched_at: datetime,
) -> dict[str, Any]:
    source_item = item
    if not item.section:
        source_item = FeedItemLike(
            title=item.title,
            url=item.url,
            section=SOURCE_NAME,
            publish_time_iso=item.publish_time_iso,
            raw=item.raw,
            gid=item.gid,
        )
    return linked_feed_item_to_row(source_item, article_id, fetched_at=fetched_at)


def build_detail_update(
    item: FeedItemLike,
    article_id: str,
    data: dict[str, Any],
    *,
    detail_fetched_at: datetime,
) -> dict[str, Any]:
    return build_linked_detail_update(
        item,
        article_id,
        data,
        detail_fetched_at=detail_fetched_at,
        default_source=SOURCE_NAME,
        render_content=html_to_markdown,
    )


__all__ = [
    "DEFAULT_UIDS_FILE",
    "FeedItemLike",
    "UidEntry",
    "build_detail_update",
    "feed_item_to_row",
    "fetch_detail",
    "html_to_markdown",
    "list_items",
    "load_uid_entries",
    "make_article_id",
    "normalize_url",
    "parse_uid",
]
