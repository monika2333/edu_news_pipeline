from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from src.adapters.http_linked_page_rows import build_detail_update as build_linked_detail_update
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

LIST_URL = "https://www.xj.chinanews.com.cn/dizhou/"
SOURCE_NAME = "中国新闻网"
ARTICLE_ID_PREFIX = "chinanewsxj:"
CANONICAL_HOST = "www.xj.chinanews.com.cn"
CHINA_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

_DETAIL_PATH_RE = re.compile(
    r"^/dizhou/\d{4}-\d{2}-\d{2}/detail-[^/?#]+\.shtml$",
    re.IGNORECASE,
)
_LIST_TIME_RE = re.compile(r"\[(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2})\]")
_DETAIL_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?)")


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


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    resolved = urljoin(LIST_URL, raw)
    parsed = urlsplit(resolved)
    hostname = (parsed.hostname or "").lower()
    if hostname in {"xj.chinanews.com.cn", CANONICAL_HOST}:
        return urlunsplit(("https", CANONICAL_HOST, parsed.path, "", ""))
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", ""))


def make_article_id(url: str) -> str:
    path = urlsplit(normalize_url(url)).path
    path = re.sub(r"\.shtml$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/+", "/", path).strip()
    return f"{ARTICLE_ID_PREFIX}{path or '/'}"


def _is_detail_url(url: str) -> bool:
    parsed = urlsplit(normalize_url(url))
    return parsed.hostname == CANONICAL_HOST and bool(_DETAIL_PATH_RE.fullmatch(parsed.path))


def _to_publish_iso(value: str, date_format: str) -> Optional[str]:
    try:
        return datetime.strptime(value, date_format).replace(tzinfo=CHINA_TZ).isoformat()
    except ValueError:
        return None


def _parse_list_html(
    html_text: str,
    *,
    existing_ids: Optional[set[str]] = None,
    existing_consecutive_stop: int = 0,
) -> list[FeedItemLike]:
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[FeedItemLike] = []
    seen_urls: set[str] = set()
    consecutive_hits = 0

    for anchor in soup.find_all("a", href=True):
        url = normalize_url(str(anchor.get("href") or ""))
        if not _is_detail_url(url) or url in seen_urls:
            continue

        title = anchor.get_text(" ", strip=True) or str(anchor.get("title") or "").strip()
        if not title:
            continue

        article_id = make_article_id(url)
        if existing_ids is not None and article_id in existing_ids:
            seen_urls.add(url)
            if existing_consecutive_stop > 0:
                consecutive_hits += 1
                if consecutive_hits >= existing_consecutive_stop:
                    return items
            continue
        consecutive_hits = 0

        row = anchor.find_parent(class_="CLtitle") or anchor.parent
        row_text = row.get_text(" ", strip=True) if isinstance(row, Tag) else ""
        time_match = _LIST_TIME_RE.search(row_text)
        publish_time_iso = (
            _to_publish_iso(time_match.group(1), "%Y.%m.%d %H:%M") if time_match else None
        )
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=SOURCE_NAME,
                publish_time_iso=publish_time_iso,
                raw={"publish_text": time_match.group(1) if time_match else None},
            )
        )
        seen_urls.add(url)

    return items


def list_items(
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    del pages  # The source exposes one rolling list page rather than numbered pages.
    if limit is not None and limit <= 0:
        return []

    session = _session()
    response = session.get(LIST_URL, timeout=15)
    response.raise_for_status()
    try:
        consecutive_stop = int(os.getenv("CHINANEWS_XJ_EXISTING_CONSECUTIVE_STOP", "5"))
    except (TypeError, ValueError):
        consecutive_stop = 5
    consecutive_stop = max(0, consecutive_stop)
    items = _parse_list_html(
        _response_text(response),
        existing_ids=existing_ids,
        existing_consecutive_stop=consecutive_stop,
    )
    return items[:limit] if limit is not None else items


def html_to_markdown(html_str: str) -> str:
    soup = BeautifulSoup(html_str or "", "html.parser")
    for unwanted in soup.select("script, style, noscript"):
        unwanted.decompose()
    for image in soup.find_all("img"):
        src = str(image.get("src") or "").strip()
        alt = str(image.get("alt") or "").strip()
        image.replace_with(f"\n\n![{alt}]({src})\n\n" if src else "")
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for block in soup.find_all(["p", "div", "h1", "h2", "h3", "li", "blockquote"]):
        block.insert_before("\n\n")
        block.insert_after("\n\n")

    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in soup.get_text().splitlines()]
    paragraphs: list[str] = []
    for line in lines:
        if line:
            paragraphs.append(line)
    return "\n\n".join(paragraphs)


def _clean_content_node(content_node: Tag) -> Tag:
    clone = BeautifulSoup(str(content_node), "html.parser")
    cleaned = clone.find()
    if not isinstance(cleaned, Tag):
        return content_node

    noise_selectors = (
        "script, style, noscript, .ad, .adEditor, .left_name, .editor, .copyright, "
        ".statement, .related, .recommend, .share, .toolbar, .nav, #function_code_page"
    )
    for unwanted in cleaned.select(noise_selectors):
        unwanted.decompose()
    noise_pattern = re.compile(r"责任编辑|【编辑[：:]|版权声明|免责声明|未经授权不得转载")
    for node in list(cleaned.find_all(["p", "div", "span"])):
        if noise_pattern.search(node.get_text(" ", strip=True)):
            node.decompose()
    return cleaned


def _strip_site_suffix(title: str) -> str:
    return re.sub(r"[-_|]\s*中新网(?:·新疆|新疆)?.*$", "", title or "").strip()


def _parse_detail_html(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    title_node = soup.select_one("#cont_1_1_2 > h1, .content > h1, h1")
    title = title_node.get_text(" ", strip=True) if title_node else ""
    if not title and soup.title:
        title = _strip_site_suffix(soup.title.get_text(" ", strip=True))

    info_node = soup.select_one(".left-time .left-t, .left-t, .left-time")
    info_text = info_node.get_text(" ", strip=True) if info_node else ""
    time_match = _DETAIL_TIME_RE.search(info_text)
    publish_time_iso = None
    if time_match:
        date_format = "%Y-%m-%d %H:%M:%S" if time_match.group(1).count(":") == 2 else "%Y-%m-%d %H:%M"
        publish_time_iso = _to_publish_iso(time_match.group(1), date_format)

    content_node = soup.select_one(".left_zw")
    if not isinstance(content_node, Tag):
        raise RuntimeError(f"Unable to find ChinaNews Xinjiang article content for {url}")
    cleaned_content = _clean_content_node(content_node)
    content_html = cleaned_content.decode_contents()
    content_markdown = html_to_markdown(content_html)
    if not content_markdown:
        raise RuntimeError(f"Empty ChinaNews Xinjiang article content for {url}")

    return {
        "title": title.strip() or None,
        "source": SOURCE_NAME,
        "publish_time": None,
        "publish_time_iso": publish_time_iso,
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
        {**data, "source": SOURCE_NAME},
        detail_fetched_at=detail_fetched_at,
        default_source=SOURCE_NAME,
        render_content=html_to_markdown,
    )


__all__ = [
    "FeedItemLike",
    "build_detail_update",
    "feed_item_to_row",
    "fetch_detail",
    "html_to_markdown",
    "list_items",
    "make_article_id",
    "normalize_url",
]
