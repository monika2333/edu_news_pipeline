from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from src.adapters.http_linked_page_rows import build_detail_update as build_linked_detail_update
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

COLUMN_BASE_URL = "https://peking.bjd.com.cn/bjhrootcolumn/system/"
DETAIL_BASE_URL = "https://peking.bjd.com.cn/content/"
DETAIL_HOST = "peking.bjd.com.cn"
SOURCE_NAME = "现代教育报"
ARTICLE_ID_PREFIX = "beijinghao:"
DEFAULT_COLUMNS_FILE = Path("config/beijinghao_author.txt")
COLUMNS_PATH_ENV = "BEIJINGHAO_COLUMNS_PATH"
CHINA_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)
_DETAIL_PATH_RE = re.compile(r"^/content/([^/?#]+)\.html$", re.IGNORECASE)


@dataclass(frozen=True)
class ColumnEntry:
    column_code: str
    page_url: str
    raw_source: str


@dataclass
class FeedItemLike:
    title: str
    url: str
    section: Optional[str]
    publish_time_iso: Optional[str]
    raw: dict[str, Any]


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
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


def column_page_url(column_code: str) -> str:
    return f"{COLUMN_BASE_URL}{column_code}"


def parse_column_code(raw: str) -> str:
    value = (raw or "").strip().lstrip("\ufeff").rstrip("/")
    if not value:
        raise ValueError("Empty Beijinghao column code")
    if re.match(r"^https?://", value, re.IGNORECASE):
        segments = [segment for segment in urlsplit(value).path.split("/") if segment]
        try:
            system_index = segments.index("system")
            value = segments[system_index + 1]
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Unable to derive Beijinghao column code from: {raw}") from exc
    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise ValueError(f"Unable to derive Beijinghao column code from: {raw}")
    return value


def load_column_entries(path: Path) -> list[ColumnEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Beijinghao column file not found: {path}")
    entries: list[ColumnEntry] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().lstrip("\ufeff")
        if not cleaned or cleaned.startswith("#"):
            continue
        column_code = parse_column_code(cleaned)
        if column_code in seen:
            continue
        seen.add(column_code)
        entries.append(
            ColumnEntry(
                column_code=column_code,
                page_url=(
                    cleaned
                    if re.match(r"^https?://", cleaned, re.IGNORECASE)
                    else column_page_url(column_code)
                ),
                raw_source=cleaned,
            )
        )
    return entries


def _resolve_columns_path() -> Path:
    configured = os.getenv(COLUMNS_PATH_ENV)
    return Path(configured.strip()) if configured and configured.strip() else DEFAULT_COLUMNS_FILE


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    resolved = urljoin(DETAIL_BASE_URL, raw)
    parsed = urlsplit(resolved)
    scheme = "https" if (parsed.hostname or "").lower() == DETAIL_HOST else parsed.scheme.lower()
    return urlunsplit((scheme, parsed.netloc.lower(), parsed.path, "", ""))


def make_article_id(value: str) -> str:
    cleaned = (value or "").strip()
    if re.match(r"^https?://|^//", cleaned, re.IGNORECASE):
        cleaned = urlsplit(normalize_url(cleaned)).path.rstrip("/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"\.(?:html|json)$", "", cleaned.strip().strip("/"), flags=re.IGNORECASE)
    if not cleaned:
        raise ValueError("Empty Beijinghao article id")
    return f"{ARTICLE_ID_PREFIX}{cleaned}"


def _is_detail_url(url: str) -> bool:
    parsed = urlsplit(normalize_url(url))
    return parsed.hostname == DETAIL_HOST and bool(_DETAIL_PATH_RE.fullmatch(parsed.path))


def _date_to_iso(value: str) -> Optional[str]:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").replace(tzinfo=CHINA_TZ).isoformat()
    except (AttributeError, ValueError):
        return None


def _timestamp_to_iso(value: Any) -> Optional[str]:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    return parsed.isoformat()


def _parse_list_html(
    html_text: str,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    items: list[FeedItemLike] = []
    seen: set[str] = set()
    for row in soup.select(".picTxt"):
        anchor = row.select_one(".title a[href]")
        if anchor is None:
            continue
        url = normalize_url(str(anchor.get("href") or ""))
        if not _is_detail_url(url):
            continue
        title = str(anchor.get("title") or "").strip() or anchor.get_text(" ", strip=True)
        if not title:
            continue
        article_id = make_article_id(url)
        if article_id in seen or (existing_ids is not None and article_id in existing_ids):
            continue
        seen.add(article_id)
        date_node = row.select_one(".other-box .data, .data")
        date_text = date_node.get_text(" ", strip=True) if date_node else ""
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=SOURCE_NAME,
                publish_time_iso=_date_to_iso(date_text),
                raw={"publish_text": date_text},
            )
        )
    return items


def _list_page_url(entry: ColumnEntry, page_number: int) -> str:
    base_url = column_page_url(entry.column_code)
    return base_url if page_number == 1 else f"{base_url}/more_{page_number}"


def list_items(
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    if limit is not None and limit <= 0:
        return []
    page_count = max(1, pages or 1)
    entries = load_column_entries(_resolve_columns_path())
    session = _session()
    items: list[FeedItemLike] = []
    known_ids = set(existing_ids or set())
    for entry in entries:
        for page_number in range(1, page_count + 1):
            response = session.get(
                _list_page_url(entry, page_number),
                headers={"Referer": entry.page_url},
                timeout=15,
            )
            response.raise_for_status()
            batch = _parse_list_html(_response_text(response), existing_ids=known_ids)
            for item in batch:
                items.append(item)
                known_ids.add(make_article_id(item.url))
                if limit is not None and len(items) >= limit:
                    return items
    return items


def html_to_markdown(html_str: str) -> str:
    soup = BeautifulSoup(html_str or "", "html.parser")
    for unwanted in soup.select(
        "script, style, noscript, iframe, .share, .recommend, .related, .editor, .copyright"
    ):
        unwanted.decompose()
    for image in soup.find_all("img"):
        src = str(image.get("src") or image.get("data-src") or "").strip()
        if src.startswith("//"):
            src = f"https:{src}"
        alt = str(image.get("alt") or "").strip()
        image.replace_with(f"\n\n![{alt}]({src})\n\n" if src else "")
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for block in soup.find_all(["p", "div", "figure", "h1", "h2", "h3", "li", "blockquote"]):
        block.insert_before("\n\n")
        block.insert_after("\n\n")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in soup.get_text().splitlines()]
    return "\n\n".join(line for line in lines if line)


def _detail_json_url(url: str) -> str:
    article_id = make_article_id(url).split(":", 1)[1]
    return f"{DETAIL_BASE_URL}{article_id}.json"


def _parse_detail_payload(payload: dict[str, Any], url: str) -> dict[str, Any]:
    code = payload.get("code")
    if code != 0:
        raise RuntimeError(
            f"Beijinghao detail API error {code}: {payload.get('message') or 'unknown error'}"
        )
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RuntimeError(f"Beijinghao detail API returned invalid data for {url}")
    content_html = str(data.get("content") or "")
    return {
        "title": str(data.get("title") or "").strip() or None,
        "source": str(data.get("columnName") or "").strip() or None,
        "publish_time": None,
        "publish_time_iso": _timestamp_to_iso(data.get("publishTime")),
        "url": normalize_url(str(data.get("url") or url)),
        "content": content_html,
        "content_markdown": html_to_markdown(content_html),
    }


def fetch_detail(url: str) -> dict[str, Any]:
    session = _session()
    response = session.get(_detail_json_url(url), headers={"Referer": normalize_url(url)}, timeout=15)
    response.raise_for_status()
    return _parse_detail_payload(response.json(), url)


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
    "ColumnEntry",
    "DEFAULT_COLUMNS_FILE",
    "FeedItemLike",
    "build_detail_update",
    "feed_item_to_row",
    "fetch_detail",
    "html_to_markdown",
    "list_items",
    "load_column_entries",
    "make_article_id",
    "normalize_url",
    "parse_column_code",
]
