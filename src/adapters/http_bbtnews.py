from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag

from src.adapters.http_linked_page_rows import build_detail_update as build_linked_detail_update
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

LOGGER = logging.getLogger(__name__)

SOURCE_NAME = "北京商报"
ARTICLE_ID_PREFIX = "bbtnews"
SITE_BASE_URL = "https://www.bbtnews.com.cn/"
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_TIMEOUT = 15.0
CHINA_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)

# 北京商报网的频道页是服务端渲染的静态列表，教育频道每页约 30 条，
# 靠 `jiaoyupd/N.shtml` 翻页。当前任务只接入教育频道；其余频道
# （汽车、健康、养老等）结构与本页相同，后续扩栏目只需往元组里加行。
COLUMNS: tuple[tuple[str, str], ...] = (
    ("教育频道", "https://www.bbtnews.com.cn/chuizhipd/chanjingzx/jiaoyupd/"),
)

# 文章 URL 形如 /2026/0920/606364.shtml；末段数字是全站唯一的 CMS 稿件 ID，
# 路径里的年月日即发布日期（站方只展示到日，无时分秒）。
_ARTICLE_PATH_RE = re.compile(r"^/(\d{4})/(\d{2})(\d{2})/(\d+)\.shtml$", re.IGNORECASE)
_ROW_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


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
    resolved = urljoin(SITE_BASE_URL, raw)
    parsed = urlsplit(resolved)
    # 站方列表页输出的文章链接是 http，全站 301 到 https；
    # 统一归一成 https，避免入库 URL 与实际可访问地址不一致。
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))


def _is_bbtnews_host(host: str) -> bool:
    return host == "bbtnews.com.cn" or host.endswith(".bbtnews.com.cn")


def _match_article_id(url: str) -> Optional[str]:
    """Return the site-wide numeric article id when the URL is an article page."""

    parsed = urlsplit(normalize_url(url))
    if not _is_bbtnews_host(parsed.hostname or ""):
        return None
    match = _ARTICLE_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None
    if _path_date(match) is None:
        return None
    return match.group(4)


def _path_date(match: re.Match[str]) -> Optional[date]:
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def make_article_id(url: str) -> str:
    article_id = _match_article_id(url)
    if article_id is None:
        raise ValueError(f"Not a bbtnews article URL: {url!r}")
    return f"{ARTICLE_ID_PREFIX}:{article_id}"


def _url_date(url: str) -> Optional[date]:
    parsed = urlsplit(normalize_url(url))
    match = _ARTICLE_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None
    return _path_date(match)


def _parse_row_date(text: str) -> Optional[date]:
    match = _ROW_DATE_RE.search(text or "")
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _midnight_iso(item_date: date) -> str:
    return datetime(item_date.year, item_date.month, item_date.day, tzinfo=CHINA_TZ).isoformat()


def _parse_list_html(
    html_text: str,
    page_url: str,
    *,
    cutoff: date,
) -> list[FeedItemLike]:
    """Parse one 教育频道 list page.

    页面上与文章同域的链接非常多（页脚友情链接、右侧栏热文等），只认左栏
    `div.c-l-wrap` 内的三个区：主列表 `div.news-feed` 的 `li` 行、头条区
    `div.news-head`、热点推荐 `div.hot-list-news`。主列表行带 `YYYY-MM-DD`
    日期（`p.others` 最后一个 span）；头条区与热点推荐行没有行内日期，
    退回 URL 路径里的日期。
    """

    soup = BeautifulSoup(html_text, "html.parser")
    container = soup.select_one(".c-l-wrap") or soup
    seen_urls: set[str] = set()
    rows: list[tuple[Optional[Tag], Optional[date]]] = []

    for li in container.select(".news-feed li"):
        others = li.select_one(".others")
        rows.append(
            (
                li.select_one("h4 a[href]") or li.find("a", href=True),
                _parse_row_date(others.get_text(" ", strip=True)) if others else None,
            )
        )

    head = container.select_one(".news-head")
    if isinstance(head, Tag):
        rows.append((head.select_one(".tit a[href]") or head.find("a", href=True), None))

    hot = container.select_one(".hot-list-news")
    if isinstance(hot, Tag):
        rows.append((hot.find("a", href=True), None))

    items: list[FeedItemLike] = []
    for anchor, row_date in rows:
        if not isinstance(anchor, Tag):
            continue
        url = normalize_url(urljoin(page_url, str(anchor.get("href") or "")))
        if url in seen_urls or _match_article_id(url) is None:
            continue
        seen_urls.add(url)

        title = _collapse_whitespace(anchor.get_text(" ", strip=True))
        if not title:
            title = _collapse_whitespace(str(anchor.get("title") or ""))
        if not title and isinstance(anchor.find("img"), Tag):
            title = _collapse_whitespace(str(anchor.find("img").get("alt") or ""))
        if not title:
            continue

        item_date = row_date or _url_date(url)
        if item_date is not None and item_date < cutoff:
            continue

        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=None,
                publish_time_iso=_midnight_iso(item_date) if item_date else None,
                raw={
                    "list_date": item_date.isoformat() if item_date else None,
                },
            )
        )

    return items


def _page_url(column_url: str, page: int) -> str:
    """第 1 页是栏目本身，第 N 页（N>=2）是 jiaoyupd/N.shtml。"""

    if page <= 1:
        return column_url
    return normalize_url(column_url).rstrip("/") + f"/{page}.shtml"


def _lookback_days() -> int:
    raw = os.getenv("BBTNEWS_LOOKBACK_DAYS")
    try:
        value = int(raw) if raw is not None else DEFAULT_LOOKBACK_DAYS
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_DAYS
    return max(0, value)


def list_items(
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> list[FeedItemLike]:
    """Walk each column's list pages and return the fresh items.

    `pages` 控制翻页深度（每页约 30 条）；调度每小时跑一轮时默认 1 页
    就够，补历史稿时由调用方显式传更大的 `pages`。
    """

    max_pages = max(1, int(pages or 1))
    if limit is not None and limit <= 0:
        return []

    current_date = today or datetime.now(CHINA_TZ).date()
    cutoff = current_date - timedelta(days=_lookback_days())

    session = _session()
    collected: list[FeedItemLike] = []
    page_notes: list[str] = []
    for name, column_url in COLUMNS:
        for page in range(1, max_pages + 1):
            page_url = _page_url(column_url, page)
            try:
                response = session.get(page_url, timeout=DEFAULT_TIMEOUT)
                response.raise_for_status()
                items = _parse_list_html(_response_text(response), page_url, cutoff=cutoff)
            except Exception as exc:
                LOGGER.warning("bbtnews list page fetch failed: %s p%s (%s)", name, page, exc)
                break
            collected.extend(items)
            page_notes.append(f"{name}p{page}={len(items)}")
            if not items:
                break

    if not page_notes:
        raise RuntimeError("All bbtnews list pages failed to load")

    merged: dict[str, FeedItemLike] = {}
    for item in collected:
        article_id = make_article_id(item.url)
        if article_id not in merged:
            merged[article_id] = item

    results: list[FeedItemLike] = []
    existing_skipped = 0
    for article_id, item in merged.items():
        if existing_ids is not None and article_id in existing_ids:
            existing_skipped += 1
            continue
        if limit is not None and len(results) >= limit:
            break
        results.append(item)

    # workers 的可见日志是 print 风格（logging 未配置 handler），
    # 与 toutiao/tencent/stdaily 一致用 stderr 输出每轮的分页汇总。
    print(
        f"[info] bbtnews pages: "
        f"{', '.join(page_notes) if page_notes else 'none loaded'}; "
        f"unique={len(merged)} new={len(results)} "
        f"existing_skipped={existing_skipped} window_cutoff={cutoff.isoformat()}",
        file=sys.stderr,
    )
    return results


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
    paragraphs = [line for line in lines if line]
    return "\n\n".join(paragraphs)


def _extract_title(soup: BeautifulSoup) -> str:
    for selector in (".article-hd h3", "h1"):
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            text = _collapse_whitespace(node.get_text(" ", strip=True))
            if text:
                return text
    if soup.title and soup.title.get_text(strip=True):
        return _collapse_whitespace(soup.title.get_text(strip=True))
    return ""


def _extract_publish_date(soup: BeautifulSoup, url: str) -> Optional[date]:
    assist = soup.select_one(".article-hd .assist .info")
    if isinstance(assist, Tag):
        parsed = _parse_row_date(assist.get_text(" ", strip=True))
        if parsed is not None:
            return parsed
    return _url_date(url)


def _parse_detail_html(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")

    content_node = soup.select_one("div.article-bd")
    if not isinstance(content_node, Tag) or not content_node.get_text(strip=True):
        raise RuntimeError(f"Unable to find bbtnews article content for {url}")

    for unwanted in content_node.select("script, style, noscript"):
        unwanted.decompose()
    for image in content_node.find_all("img"):
        src = str(image.get("src") or "").strip()
        if src:
            image["src"] = urljoin(url, src)

    content_html = content_node.decode_contents()
    content_markdown = html_to_markdown(content_html)
    if not content_markdown:
        raise RuntimeError(f"Empty bbtnews article content for {url}")

    publish_date = _extract_publish_date(soup, url)

    return {
        "title": _extract_title(soup) or None,
        # 来源固定为"北京商报"；实际署名媒体交给下游 llm_source 识别。
        "source": SOURCE_NAME,
        "publish_time": None,
        "publish_time_iso": _midnight_iso(publish_date) if publish_date else None,
        "url": normalize_url(url),
        "content": content_html,
        "content_markdown": content_markdown,
    }


def fetch_detail(url: str) -> dict[str, Any]:
    session = _session()
    response = session.get(normalize_url(url), timeout=DEFAULT_TIMEOUT)
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
    "COLUMNS",
    "DEFAULT_LOOKBACK_DAYS",
    "FeedItemLike",
    "SOURCE_NAME",
    "build_detail_update",
    "fetch_detail",
    "feed_item_to_row",
    "html_to_markdown",
    "list_items",
    "make_article_id",
    "normalize_url",
]
