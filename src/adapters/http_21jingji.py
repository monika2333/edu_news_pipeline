"""21 经济网（21 世纪经济报道官网）栏目抓取 adapter。

站点按「频道栏目页 → 文章详情页」组织：栏目页第一页 20 条，列表里只给
「3小时前」这类相对时间，精确发布时间在文章正文头部。栏目页没有服务端
翻页（「查看更多」走的前端接口已经 404），因此每个栏目每轮只取第一页。

账号模型与控制台一致：一条 crawl_account 对应一个栏目页 URL；文章落库的
``source`` 固定为报名「21世纪经济报道」，与北京号所有稿件都记「北京日报」
是同一条规则，不随栏目名变化。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from src.adapters.http_common import (
    build_session,
    decode_response,
    html_to_markdown as common_html_to_markdown,
)
from src.adapters.http_linked_page_rows import (
    build_detail_update as build_linked_detail_update,
)
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.21jingji.com"
CHANNEL_HOST = "www.21jingji.com"
SOURCE_KEY = "21jingji"
SOURCE_NAME = "21世纪经济报道"
ARTICLE_ID_PREFIX = f"{SOURCE_KEY}:"
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT = 15.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)
ARTICLE_PATH_RE = re.compile(r"^/article/.+\.html?$", re.IGNORECASE)
ARTICLE_HOSTS = frozenset({CHANNEL_HOST, "21jingji.com", "m.21jingji.com"})
PUBLISH_TIME_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})")


@dataclass(frozen=True)
class ChannelEntry:
    """控制台「账号=栏目」模型里的一条 21 经济网栏目（栏目列表页 URL）。"""

    url: str
    raw_source: str = ""


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
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "Referer": f"{BASE_URL}/",
        }
    )


def parse_channel_input(raw: str) -> ChannelEntry:
    """把管理员输入的栏目地址规整为 https + 站内 ``/channel/<栏目>/`` URL。

    接受完整 URL、协议相对地址（``//www.21jingji.com/...``）或纯栏目路径
    （``/channel/politics/``）；拒绝其它域名与文章页，列表解析器只适配
    这个站点的栏目版式。
    """

    cleaned = (raw or "").strip().lstrip("\ufeff")
    if not cleaned:
        raise ValueError("Empty 21jingji channel URL")
    candidate = cleaned
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = f"{BASE_URL}/{candidate.lstrip('/')}"
    parsed = urlsplit(candidate)
    if (parsed.hostname or "").lower() != CHANNEL_HOST:
        raise ValueError(f"21经济网栏目地址必须是 {CHANNEL_HOST} 域名：{cleaned}")
    path = parsed.path.rstrip("/")
    if path.startswith("/article/") or path.lower().endswith((".html", ".htm")):
        raise ValueError(f"请填栏目列表页地址，不要填文章页：{cleaned}")
    if not path.startswith("/channel/") or path.count("/") < 2:
        raise ValueError(f"请填 21经济网频道栏目页地址（/channel/...）：{cleaned}")
    normalized = urlunsplit(("https", CHANNEL_HOST, f"{path}/", "", ""))
    return ChannelEntry(url=normalized, raw_source=cleaned)


def normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = f"https:{raw}"
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = urljoin(f"{BASE_URL}/", raw)
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").lower()
    scheme = "https" if host in {CHANNEL_HOST, "21jingji.com", "m.21jingji.com"} else parsed.scheme.lower()
    return urlunsplit((scheme, parsed.netloc.lower(), parsed.path, "", ""))


def _is_article_url(url: str) -> bool:
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower() not in ARTICLE_HOSTS:
        return False
    return bool(ARTICLE_PATH_RE.match(parsed.path or ""))


def make_article_id(url: str) -> str:
    """文章 ID 取文章 URL 最后一段（herald 的 hash），跨日期稳定。"""

    path = urlsplit(normalize_url(url)).path.rstrip("/")
    slug = path.rsplit("/", 1)[-1] if path else ""
    slug = re.sub(r"\.s?html?$", "", slug, flags=re.IGNORECASE)
    if not slug:
        raise ValueError(f"Empty 21jingji article id for URL: {url}")
    return f"{ARTICLE_ID_PREFIX}{slug}"


def _parse_list_html(
    html_text: str,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    container = soup.select_one("div.col-l div.news")
    if container is None:
        return []
    items: list[FeedItemLike] = []
    seen: set[str] = set(existing_ids or set())
    for anchor in container.select("a[href]"):
        url = normalize_url(str(anchor.get("href") or ""))
        if not _is_article_url(url):
            continue
        title = str(anchor.get("title") or "").strip()
        if not title:
            heading = anchor.select_one(".title h2, h2")
            title = heading.get_text(" ", strip=True) if heading is not None else ""
        if not title:
            continue
        article_id = make_article_id(url)
        if article_id in seen:
            continue
        seen.add(article_id)
        time_node = anchor.select_one(".title span")
        items.append(
            FeedItemLike(
                title=title,
                url=url,
                section=SOURCE_NAME,
                publish_time_iso=None,
                raw={
                    "publish_text": (
                        time_node.get_text(" ", strip=True)
                        if time_node is not None
                        else ""
                    )
                },
            )
        )
    return items


def list_items(
    entries: list[ChannelEntry],
    limit: Optional[int] = None,
    pages: Optional[int] = None,
    *,
    existing_ids: Optional[set[str]] = None,
) -> list[FeedItemLike]:
    if limit is not None and limit <= 0:
        return []
    if pages is not None and pages > 1:
        LOGGER.warning(
            "21jingji channel pages have no server-side pagination; "
            "only the first page is fetched."
        )
    session = _session()
    items: list[FeedItemLike] = []
    known_ids = set(existing_ids or set())
    for entry in entries:
        response = session.get(entry.url, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        for item in _parse_list_html(
            decode_response(response),
            existing_ids=known_ids,
        ):
            article_id = make_article_id(item.url)
            if article_id in known_ids:
                continue
            known_ids.add(article_id)
            items.append(item)
            if limit is not None and len(items) >= limit:
                return items
    return items


def html_to_markdown(html_str: str) -> str:
    return common_html_to_markdown(html_str)


def _extract_publish_time_iso(soup: BeautifulSoup) -> Optional[str]:
    node = soup.select_one(".newsAuthor .author-infos span") or soup.select_one(
        ".newsAuthor"
    )
    text = node.get_text(" ", strip=True) if node is not None else ""
    match = PUBLISH_TIME_RE.search(text)
    if match is None:
        return None
    year, month, day, hour, minute = match.groups()
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            tzinfo=CHINA_TZ,
        ).isoformat()
    except ValueError:
        return None


def _parse_detail_html(html_text: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading is not None else ""
    if not title and soup.title is not None:
        title = re.sub(r"\s*-\s*21财经\s*$", "", soup.title.get_text(" ", strip=True))
    if not title:
        raise RuntimeError(f"Unable to find 21jingji article title for {url}")
    content = soup.select_one(".txtContent .main_content")
    if content is None:
        raise RuntimeError(f"Unable to find 21jingji article content for {url}")
    content_markdown = html_to_markdown(str(content))
    if not content_markdown:
        raise RuntimeError(f"Empty 21jingji article content for {url}")
    return {
        "title": title,
        "source": SOURCE_NAME,
        "publish_time": None,
        "publish_time_iso": _extract_publish_time_iso(soup),
        "url": normalize_url(url),
        "content": str(content),
        "content_markdown": content_markdown,
    }


def fetch_detail(url: str) -> dict[str, Any]:
    session = _session()
    response = session.get(
        normalize_url(url),
        headers={"Referer": f"{BASE_URL}/"},
        timeout=DEFAULT_TIMEOUT,
    )
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
    "BASE_URL",
    "CHANNEL_HOST",
    "ChannelEntry",
    "DEFAULT_TIMEOUT",
    "FeedItemLike",
    "SOURCE_KEY",
    "SOURCE_NAME",
    "build_detail_update",
    "feed_item_to_row",
    "fetch_detail",
    "html_to_markdown",
    "list_items",
    "make_article_id",
    "normalize_url",
    "parse_channel_input",
]
