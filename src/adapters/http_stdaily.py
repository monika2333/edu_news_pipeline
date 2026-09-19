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

SOURCE_NAME = "科技日报"
ARTICLE_ID_PREFIX = "stdaily"
SITE_BASE_URL = "https://www.stdaily.com/"
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_TIMEOUT = 15.0
CHINA_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)

# 科技日报（中国科技网）的滚动新闻是全站滚动更新的稿件流，其余频道（时政、
# 热点、地方等）的稿件基本都会汇入这里；与 xinhua 不同，本页有服务端分页，
# 每页约 16 条，靠 `pages` 参数控制翻页深度。
COLUMNS: tuple[tuple[str, str], ...] = (
    ("滚动新闻", "https://www.stdaily.com/web/gdxw/node_324.html"),
)

# 文章 URL 形如 /web/gdxw/2026-09/19/content_584227.html；
# content 编号是全站唯一的稿件 ID，同一稿件在不同频道下编号相同，
# 因此 article_id 只取编号，跨频道列表天然去重。
_ARTICLE_PATH_RE = re.compile(
    r"^/web/[^/]+/(\d{4})-(\d{2})/(\d{2})/content_(\d+)\.html$", re.IGNORECASE
)
_LIST_PAGE_RE = re.compile(r"^(?P<base>.*/node_\d+)(?:_(?P<page>\d+))?\.html$", re.IGNORECASE)
_DETAIL_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


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
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "")
    )


def _is_stdaily_host(host: str) -> bool:
    return host == "stdaily.com" or host.endswith(".stdaily.com")


def _match_article_path(url: str) -> Optional[str]:
    """Return the site-wide numeric content id when the URL is an article page."""

    parsed = urlsplit(normalize_url(url))
    if not _is_stdaily_host(parsed.hostname or ""):
        return None
    match = _ARTICLE_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None
    return match.group(4)


def make_article_id(url: str) -> str:
    slug = _match_article_path(url)
    if slug is None:
        raise ValueError(f"Not a stdaily article URL: {url!r}")
    return f"{ARTICLE_ID_PREFIX}:{slug}"


def _url_date(url: str) -> Optional[date]:
    match = _ARTICLE_PATH_RE.fullmatch(urlsplit(normalize_url(url)).path)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_row_datetime(text: str) -> Optional[datetime]:
    match = _DETAIL_TIME_RE.search(text or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _parse_list_html(
    html_text: str,
    page_url: str,
    *,
    cutoff: date,
) -> list[FeedItemLike]:
    """Parse one 滚动新闻 list page.

    全页解析会把侧栏"热门点击"等区片的稿件混进来（实测首页有 25 条），
    所以只认 `div.f_lieb_list` 容器里的 `<dl>` 行；每行带完整发布时间，
    缺失时退回 URL 里的日期。
    """

    soup = BeautifulSoup(html_text, "html.parser")
    container = soup.select_one(".f_lieb_list") or soup
    items: list[FeedItemLike] = []
    seen_urls: set[str] = set()

    for row in container.find_all("dl"):
        anchor = row.find("a", href=True)
        if not isinstance(anchor, Tag):
            continue
        url = normalize_url(urljoin(page_url, str(anchor.get("href") or "")))
        if url in seen_urls or _match_article_path(url) is None:
            continue
        seen_urls.add(url)

        title = _collapse_whitespace(anchor.get_text(" ", strip=True))
        if not title:
            title = _collapse_whitespace(str(anchor.get("title") or ""))
        if not title:
            continue

        row_time = _parse_row_datetime(row.get_text(" ", strip=True))
        item_date = row_time.date() if row_time else _url_date(url)
        if item_date is not None and item_date < cutoff:
            continue

        if row_time is not None:
            iso = row_time.replace(tzinfo=CHINA_TZ).isoformat()
        elif item_date is not None:
            iso = datetime(
                item_date.year, item_date.month, item_date.day, tzinfo=CHINA_TZ
            ).isoformat()
        else:
            iso = None

        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=None,
                publish_time_iso=iso,
                raw={
                    "list_time": row_time.isoformat() if row_time else None,
                    "list_date": item_date.isoformat() if item_date else None,
                },
            )
        )

    return items


def _page_url(column_url: str, page: int) -> str:
    """第 1 页是栏目本身，第 N 页（N>=2）是 node_XXX_N.html。"""

    if page <= 1:
        return column_url
    match = _LIST_PAGE_RE.fullmatch(urlsplit(normalize_url(column_url)).path)
    if not match:
        return column_url
    base = match.group("base")
    parsed = urlsplit(normalize_url(column_url))
    return urlunsplit((parsed.scheme, parsed.netloc, f"{base}_{page}.html", "", ""))


def _lookback_days() -> int:
    raw = os.getenv("STDAILY_LOOKBACK_DAYS")
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

    `pages` 控制翻页深度（每页约 16 条）；调度每小时跑一轮时默认 1 页
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
                LOGGER.warning("stdaily list page fetch failed: %s p%s (%s)", name, page, exc)
                break
            collected.extend(items)
            page_notes.append(f"{name}p{page}={len(items)}")
            if not items:
                break

    if not page_notes:
        raise RuntimeError("All stdaily list pages failed to load")

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
    # 与 toutiao/tencent/xinhua 一致用 stderr 输出每轮的分页汇总。
    print(
        f"[info] stdaily pages: "
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
        # 科技日报的图片说明放在 topic 属性，alt 常为空。
        alt = str(image.get("alt") or "").strip() or str(image.get("topic") or "").strip()
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
    for h1 in soup.find_all("h1"):
        text = _collapse_whitespace(h1.get_text(" ", strip=True))
        if text:
            return text
    if soup.title and soup.title.get_text(strip=True):
        return _collapse_whitespace(soup.title.get_text(strip=True))
    return ""


def _extract_publish_time_iso(soup: BeautifulSoup) -> Optional[str]:
    scope = soup.select_one(".time1") or soup
    parsed = _parse_row_datetime(scope.get_text(" ", strip=True))
    if parsed is None:
        return None
    return parsed.replace(tzinfo=CHINA_TZ).isoformat()


def _parse_detail_html(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")

    content_node = soup.select_one("#printContent .content")
    if not isinstance(content_node, Tag) or not content_node.get_text(strip=True):
        raise RuntimeError(f"Unable to find stdaily article content for {url}")

    for unwanted in content_node.select("script, style, noscript"):
        unwanted.decompose()
    for image in content_node.find_all("img"):
        src = str(image.get("src") or "").strip()
        if src:
            image["src"] = urljoin(url, src)

    content_html = content_node.decode_contents()
    content_markdown = html_to_markdown(content_html)
    if not content_markdown:
        raise RuntimeError(f"Empty stdaily article content for {url}")

    return {
        "title": _extract_title(soup) or None,
        # 来源固定为"科技日报"；实际署名媒体交给下游 llm_source 识别。
        "source": SOURCE_NAME,
        "publish_time": None,
        "publish_time_iso": _extract_publish_time_iso(soup),
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
