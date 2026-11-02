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

from src.adapters.http_common import build_session, decode_response, html_to_markdown
from src.adapters.http_linked_page_rows import build_detail_update as build_linked_detail_update
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

LOGGER = logging.getLogger(__name__)

SOURCE_NAME = "新华网"
ARTICLE_ID_PREFIX = "xinhua"
CHANNEL_BASE_URL = "http://bj.news.cn/"
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_TIMEOUT = 15.0
CHINA_TZ = timezone(timedelta(hours=8))
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)

# 北京频道没有全站滚动页，只有按栏目划分的列表页。这里逐个列出文章栏目
# （含不在导航栏里的"聚焦"），不按主题筛选：同一稿件可能只出现在其中某个
# 栏目（教育新闻常落在"聚焦"而非"科教"），漏一个栏目就漏稿。
# 导航里的"专题"(/zt) 不在内：它只链到 /bdzt/、/hyzt/ 等专题页，不是文章列表。
# 每轮只请求各栏目 index 首屏，不处理"显示更多"或翻页。
COLUMNS: tuple[tuple[str, str], ...] = (
    ("新华社记者看北京", "http://bj.news.cn/jz/index.htm"),
    ("京华新声", "http://bj.news.cn/jhxs/index.htm"),
    ("要闻", "http://bj.news.cn/gdxw/index.htm"),
    ("政务", "http://bj.news.cn/bjzw/index.htm"),
    ("京津冀", "http://bj.news.cn/jjj/index.htm"),
    ("社会", "http://bj.news.cn/sh/index.htm"),
    ("文旅", "http://bj.news.cn/wl/index.htm"),
    ("科教", "http://bj.news.cn/kj/index.htm"),
    ("财经", "http://bj.news.cn/cj/index.htm"),
    ("视频", "http://bj.news.cn/sp/index.htm"),
    ("访谈", "http://bj.news.cn/ft/index.htm"),
    ("图说", "http://bj.news.cn/tj/index.htm"),
    ("视觉", "http://bj.news.cn/tp/index.htm"),
    ("资讯联播", "http://bj.news.cn/xxgj/index.html"),
    ("聚焦", "http://bj.news.cn/jj/index.htm"),
)

EDUCATION_CHANNEL_BASE_URL = "https://education.news.cn/"
EDUCATION_DEFAULT_LOOKBACK_DAYS = 3

# 教育频道只接以下 4 个栏目：图文列表、仍在更新。每轮只请求首屏，
# 不处理"加载更多"和 ds_*.json 翻页。
# 其余栏目不要好心加回来（2026-11-02 核对）：
# - 权威发布：首屏不按时间倒序（置顶旧稿），首页已出现的新稿不在首屏
#   前 15 条内，只抓首屏会漏稿——除非做翻页，否则不能接。
# - 国际教育、商科教育、名校大家谈、教育对话、教育观察等：已停更。
EDUCATION_CHANNELS: tuple[tuple[str, str], ...] = (
    ("每日要闻", "https://education.news.cn/mryw/index.htm"),
    ("基础教育", "https://education.news.cn/jcjy/index.htm"),
    ("高等教育", "https://education.news.cn/gdjy/index.htm"),
    ("职业教育", "https://education.news.cn/eduzyjy/index.htm"),
)

# 文章路径允许日期前带一段小写栏目名（如 www.news.cn/legal/20261020/<slug>/c.html，
# 教育频道每日要闻首屏真实存在）。栏目段保持小写敏感；slug 段沿用大小写不敏感
# （历史页面存在大写十六进制 slug，ID 一律 .lower() 归一）。
# 分组序号保持不变：group(1)=日期，group(2)=slug。
_ARTICLE_PATH_RE = re.compile(r"^/(?:[a-z]+/)?(\d{8})/(?i:([0-9a-f]{32}))/c\.html$")
_LIST_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DETAIL_TIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


@dataclass
class FeedItemLike:
    title: str
    url: str
    section: Optional[str]
    publish_time_iso: Optional[str]
    raw: dict[str, Any]


def _session() -> requests.Session:
    return build_session(
        {
            "Accept-Language": "zh-CN,zh;q=0.9",
            "User-Agent": USER_AGENT,
        }
    )


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    resolved = urljoin(CHANNEL_BASE_URL, raw)
    parsed = urlsplit(resolved)
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, "", "")
    )


def _is_news_cn_host(host: str) -> bool:
    return host == "news.cn" or host.endswith(".news.cn")


def _match_article_path(url: str) -> Optional[str]:
    """Return the 32-hex article slug when the URL is a recognized article page."""

    parsed = urlsplit(normalize_url(url))
    if not _is_news_cn_host(parsed.hostname or ""):
        return None
    match = _ARTICLE_PATH_RE.fullmatch(parsed.path)
    if not match:
        return None
    return match.group(2).lower()


def make_article_id(url: str) -> str:
    slug = _match_article_path(url)
    if slug is None:
        raise ValueError(f"Not a xinhua article URL: {url!r}")
    return f"{ARTICLE_ID_PREFIX}:{slug}"


def _parse_list_date(text: str) -> Optional[date]:
    match = _LIST_DATE_RE.search(text or "")
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _url_date(url: str) -> Optional[date]:
    raw = urlsplit(normalize_url(url)).path
    # 与 _ARTICLE_PATH_RE 对齐：允许日期前带一段小写栏目名，否则带前缀的
    # 文章地址取不到日期，回溯窗口对它们不生效。
    match = re.match(r"^/(?:[a-z]+/)?(\d{4})(\d{2})(\d{2})/", raw)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _parse_list_html(
    html_text: str,
    page_url: str,
    *,
    cutoff: date,
) -> tuple[list[FeedItemLike], int]:
    """Parse one column index page.

    Returns the items whose list date survives the lookback window, plus how
    many article links were dropped for being too old.
    """

    soup = BeautifulSoup(html_text, "html.parser")
    items: list[FeedItemLike] = []
    seen_urls: set[str] = set()
    dropped_by_window = 0

    for anchor in soup.find_all("a", href=True):
        url = normalize_url(urljoin(page_url, str(anchor.get("href") or "")))
        if url in seen_urls or _match_article_path(url) is None:
            continue
        seen_urls.add(url)

        title = anchor.get_text(" ", strip=True) or str(anchor.get("title") or "").strip()
        if not title:
            continue

        row = anchor.find_parent("li") or anchor.parent
        row_text = row.get_text(" ", strip=True) if isinstance(row, Tag) else ""
        item_date = _parse_list_date(row_text) or _url_date(url)
        if item_date is not None and item_date < cutoff:
            dropped_by_window += 1
            continue

        iso = (
            datetime(item_date.year, item_date.month, item_date.day, tzinfo=CHINA_TZ).isoformat()
            if item_date is not None
            else None
        )
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=None,
                publish_time_iso=iso,
                raw={"list_date": item_date.isoformat() if item_date else None},
            )
        )

    return items, dropped_by_window


def _merge_columns_to_unique(column_items: list[FeedItemLike]) -> list[FeedItemLike]:
    """Merge every column's items by article id, first column wins."""

    merged: dict[str, FeedItemLike] = {}
    for item in column_items:
        article_id = make_article_id(item.url)
        if article_id not in merged:
            merged[article_id] = item
    return list(merged.values())


def _lookback_days() -> int:
    raw = os.getenv("XINHUA_LOOKBACK_DAYS")
    try:
        value = int(raw) if raw is not None else DEFAULT_LOOKBACK_DAYS
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_DAYS
    return max(0, value)


def _education_lookback_days() -> int:
    # 与 XINHUA_LOOKBACK_DAYS 同义但独立：教育频道可单独调整回溯窗口。
    raw = os.getenv("XINHUA_EDU_LOOKBACK_DAYS")
    try:
        value = int(raw) if raw is not None else EDUCATION_DEFAULT_LOOKBACK_DAYS
    except (TypeError, ValueError):
        return EDUCATION_DEFAULT_LOOKBACK_DAYS
    return max(0, value)


def list_items(
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> list[FeedItemLike]:
    """Fetch every column's index page once and return the fresh items.

    `pages` is accepted for the shared linked-page flow but ignored: each
    column only exposes its first screen, which is enough at this source's
    posting rate.
    """

    del pages
    if limit is not None and limit <= 0:
        return []

    current_date = today or datetime.now(CHINA_TZ).date()
    cutoff = current_date - timedelta(days=_lookback_days())

    session = _session()
    collected: list[FeedItemLike] = []
    column_notes: list[str] = []
    window_dropped = 0
    failed_columns = 0
    for name, column_url in COLUMNS:
        try:
            response = session.get(column_url, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            items, dropped = _parse_list_html(
                decode_response(response),
                column_url,
                cutoff=cutoff,
            )
        except Exception as exc:
            failed_columns += 1
            LOGGER.warning("xinhua column fetch failed: %s (%s)", name, exc)
            continue
        window_dropped += dropped
        collected.extend(items)
        column_notes.append(f"{name}={len(items)}")

    if failed_columns == len(COLUMNS):
        raise RuntimeError(f"All {len(COLUMNS)} xinhua columns failed to load")

    merged = _merge_columns_to_unique(collected)
    results: list[FeedItemLike] = []
    existing_skipped = 0
    for item in merged:
        article_id = make_article_id(item.url)
        if existing_ids is not None and article_id in existing_ids:
            existing_skipped += 1
            continue
        if limit is not None and len(results) >= limit:
            break
        results.append(item)

    # workers 的可见日志是 print 风格（logging 未配置 handler），
    # 与 toutiao/tencent 一致用 stderr 输出每轮的栏目汇总。
    print(
        f"[info] xinhua columns: "
        f"{', '.join(column_notes) if column_notes else 'none loaded'}; "
        f"unique={len(merged)} new={len(results)} "
        f"existing_skipped={existing_skipped} window_dropped={window_dropped}",
        file=sys.stderr,
    )
    return results


def _parse_education_list_html(
    html_text: str,
    page_url: str,
    *,
    cutoff: date,
) -> tuple[list[FeedItemLike], int, int]:
    """Parse one education channel index page, ``#content-list`` items only.

    教育频道条目是图文结构，第一个链接是图片、share 区还有微博分享链接，
    不能复用全页扫链接的 _parse_list_html（它会在检查标题前登记 URL，
    图片在前的整条会被丢掉）。只认 #content-list 内的 .item，侧栏的
    "每日要闻""热点专题"等块不进结果。

    Returns the items inside the lookback window, how many article entries
    were dropped for being too old, and how many .item entries yielded no
    article link at all (share links are not counted). Raises ValueError
    when the expected container is missing or when not a single article
    entry can be parsed before window filtering—both mean the page has
    changed and the caller counts that channel as failed; a page whose
    articles all fall outside the window is NOT a failure.
    """

    soup = BeautifulSoup(html_text, "html.parser")
    container = soup.select_one("#content-list")
    if container is None:
        raise ValueError(f"education channel page has no #content-list: {page_url}")

    items: list[FeedItemLike] = []
    seen_urls: set[str] = set()
    window_dropped = 0
    items_without_article = 0
    articles_seen = 0

    for entry in container.select(".item"):
        tit_anchor = entry.select_one(".tit a[href]")
        item_url: Optional[str] = None
        tit_title = ""
        if tit_anchor is not None:
            tit_url = normalize_url(urljoin(page_url, str(tit_anchor.get("href") or "")))
            if _match_article_path(tit_url) is not None:
                item_url = tit_url
                tit_title = tit_anchor.get_text(" ", strip=True)

        # 标题兜底：条目内第一个有文字的链接（图文条目的第一个链接是图片，
        # 没有文字，天然跳过）。分享链接等非文章地址不参与、也不计数。
        fallback_title = ""
        for anchor in entry.find_all("a", href=True):
            url = normalize_url(urljoin(page_url, str(anchor.get("href") or "")))
            if _match_article_path(url) is None:
                continue
            if item_url is None:
                item_url = url
            if not fallback_title:
                text = anchor.get_text(" ", strip=True)
                if text:
                    fallback_title = text

        if item_url is None:
            # 整条找不到文章链接是改版信号，单独计数进汇总日志。
            items_without_article += 1
            continue
        articles_seen += 1
        if item_url in seen_urls:
            continue
        title = tit_title or fallback_title
        if not title:
            continue
        seen_urls.add(item_url)

        # 列表日期以 URL 中的 YYYYMMDD 为准；真实页面存在列表显示日期与
        # URL 错位一天的情况，.time 只作兜底（置顶条目没有 .time）。
        item_date = _url_date(item_url)
        if item_date is None:
            time_node = entry.select_one(".time")
            item_date = _parse_list_date(
                time_node.get_text(" ", strip=True) if time_node else ""
            )
        if item_date is not None and item_date < cutoff:
            window_dropped += 1
            continue

        iso = (
            datetime(item_date.year, item_date.month, item_date.day, tzinfo=CHINA_TZ).isoformat()
            if item_date is not None
            else None
        )
        items.append(
            FeedItemLike(
                title=title,
                url=item_url,
                section=None,
                publish_time_iso=iso,
                raw={"list_date": item_date.isoformat() if item_date else None},
            )
        )

    # 容器在但一条文章都解析不出（含没有任何 .item）：与容器缺失同级，
    # 否则改版会表现为长期静默 0 条。窗口把文章全滤掉不算（articles_seen
    # 在窗口过滤前统计）。
    if articles_seen == 0:
        raise ValueError(
            f"education channel page has no parseable article entries: {page_url}"
        )

    return items, window_dropped, items_without_article


def list_education_items(
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
    today: Optional[date] = None,
) -> list[FeedItemLike]:
    """Fetch every education channel's index page once and return fresh items.

    `pages` is accepted for the shared linked-page flow but ignored: each
    channel only exposes its first screen ("加载更多"/ds_*.json 不处理).
    """

    del pages
    if limit is not None and limit <= 0:
        return []

    current_date = today or datetime.now(CHINA_TZ).date()
    cutoff = current_date - timedelta(days=_education_lookback_days())

    session = _session()
    collected: list[FeedItemLike] = []
    channel_notes: list[str] = []
    window_dropped = 0
    items_without_article = 0
    failed_channels = 0
    for name, channel_url in EDUCATION_CHANNELS:
        try:
            response = session.get(channel_url, timeout=DEFAULT_TIMEOUT)
            response.raise_for_status()
            items, dropped, without_article = _parse_education_list_html(
                decode_response(response),
                channel_url,
                cutoff=cutoff,
            )
        except Exception as exc:
            # 页面改版不能表现为长期 0 条且没有报警：单频道失败保留其余频道，
            # 但必须以 stderr [warn] 输出（worker 里 LOGGER.warning 不可见）。
            failed_channels += 1
            print(
                f"[warn] xinhua_edu channel fetch failed: {name} ({exc})",
                file=sys.stderr,
            )
            LOGGER.warning("xinhua_edu channel fetch failed: %s (%s)", name, exc)
            continue
        window_dropped += dropped
        items_without_article += without_article
        collected.extend(items)
        channel_notes.append(f"{name}={len(items)}")

    if failed_channels == len(EDUCATION_CHANNELS):
        raise RuntimeError(
            f"All {len(EDUCATION_CHANNELS)} xinhua education channels failed to load"
        )

    merged = _merge_columns_to_unique(collected)
    results: list[FeedItemLike] = []
    existing_skipped = 0
    for item in merged:
        article_id = make_article_id(item.url)
        if existing_ids is not None and article_id in existing_ids:
            existing_skipped += 1
            continue
        if limit is not None and len(results) >= limit:
            break
        results.append(item)

    print(
        f"[info] xinhua_edu channels: "
        f"{', '.join(channel_notes) if channel_notes else 'none loaded'}; "
        f"unique={len(merged)} new={len(results)} "
        f"existing_skipped={existing_skipped} window_dropped={window_dropped} "
        f"items_without_article={items_without_article}",
        file=sys.stderr,
    )
    return results


def _extract_title(soup: BeautifulSoup) -> str:
    for h1 in soup.find_all("h1"):
        text = h1.get_text(" ", strip=True)
        if text:
            return text
    if soup.title and soup.title.get_text(strip=True):
        return re.sub(r"-新华网\s*$", "", soup.title.get_text(strip=True)).strip()
    return ""


def _extract_publish_time_iso(soup: BeautifulSoup) -> Optional[str]:
    scope = soup.select_one(".mheader .info") or soup
    match = _DETAIL_TIME_RE.search(scope.get_text(" ", strip=True))
    if not match:
        return None
    try:
        parsed = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=CHINA_TZ).isoformat()


def _parse_detail_html(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")

    content_node = soup.select_one("#detailContent")
    if not isinstance(content_node, Tag) or not content_node.get_text(strip=True):
        raise RuntimeError(f"Unable to find xinhua article content for {url}")

    for unwanted in content_node.select("script, style, noscript"):
        unwanted.decompose()
    for image in content_node.find_all("img"):
        src = str(image.get("src") or "").strip()
        if src:
            image["src"] = urljoin(url, src)

    content_html = content_node.decode_contents()
    content_markdown = html_to_markdown(content_html)
    if not content_markdown:
        raise RuntimeError(f"Empty xinhua article content for {url}")

    return {
        "title": _extract_title(soup) or None,
        # 来源固定为"新华网"；实际署名媒体交给下游 llm_source 识别。
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
    return _parse_detail_html(decode_response(response), url)


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
    "EDUCATION_CHANNELS",
    "EDUCATION_DEFAULT_LOOKBACK_DAYS",
    "FeedItemLike",
    "SOURCE_NAME",
    "build_detail_update",
    "fetch_detail",
    "feed_item_to_row",
    "html_to_markdown",
    "list_education_items",
    "list_items",
    "make_article_id",
    "normalize_url",
]
