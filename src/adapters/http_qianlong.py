from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunsplit

import requests
from bs4 import BeautifulSoup, NavigableString, Tag  # type: ignore
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://beijing.qianlong.com/"
DEFAULT_EDU_BASE_URL = "https://edu.qianlong.com/"
DEFAULT_BASE_URLS: Tuple[str, ...] = (DEFAULT_BASE_URL, DEFAULT_EDU_BASE_URL)
# 每栏目每轮默认只抓列表第 1 页：新栏目没有任何已入库条目，existing 截停
# 无法触发，必须靠硬页数上限防止把栏目历史存档整库翻完。QIANLONG_PAGES
# 仅作为上调覆盖手段。
DEFAULT_MAX_PAGES: int = 1
DEFAULT_TIMEOUT = 20.0
DEFAULT_DELAY = 0.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)
PUBLISH_TIME_PATTERN = re.compile(r"20\d{2}-\d{1,2}-\d{1,2}\s+\d{2}:\d{2}")
CHINA_TZ = timezone(timedelta(hours=8))
SOURCE_NAME = "千龙网"
CHANNEL_HOST_SUFFIX = "qianlong.com"


@dataclass(frozen=True)
class ChannelEntry:
    """控制台「账号=栏目」模型里的一条千龙网栏目（栏目列表页 URL）。"""

    url: str
    raw_source: str = ""


def parse_channel_input(raw: str) -> ChannelEntry:
    """把管理员输入的栏目地址规整为 https 的规范 URL。

    千龙网各频道分散在子域名下（beijing / edu 等），只要 host 是
    qianlong.com 或其子域名即可；根路径栏目（如 beijing.qianlong.com）
    合法，这点与 chinadaily 的目录型栏目不同。
    """
    cleaned = (raw or "").strip().lstrip("\ufeff")
    if not cleaned:
        raise ValueError("Empty Qianlong channel URL")
    candidate = cleaned
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = f"https://{cleaned}"
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if host != CHANNEL_HOST_SUFFIX and not host.endswith("." + CHANNEL_HOST_SUFFIX):
        raise ValueError(f"千龙网栏目地址必须是 {CHANNEL_HOST_SUFFIX} 域名：{cleaned}")
    normalized = urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
    return ChannelEntry(url=normalized, raw_source=cleaned)


@dataclass
class QianlongArticle:
    """Structured article entity for 千龙网 output."""

    title: str
    url: str
    publish_time: Optional[int]
    publish_time_iso: Optional[datetime]
    content_markdown: str
    raw_publish_text: Optional[str]


def _create_session(timeout: float) -> requests.Session:
    """Create a configured requests session with retries."""
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 503, 504),
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": DEFAULT_BASE_URL,
            "Connection": "keep-alive",
        }
    )
    session.request = _wrap_timeout(session.request, timeout)  # type: ignore[assignment]
    return session


def _wrap_timeout(func, timeout: float):
    """Attach a default timeout to session requests."""

    def wrapper(method, url, **kwargs):
        if "timeout" not in kwargs:
            kwargs["timeout"] = timeout
        return func(method, url, **kwargs)

    return wrapper


def _iter_listing_urls(base_url: str, max_pages: Optional[int]) -> Iterable[str]:
    yield base_url
    page = 2
    while True:
        if max_pages is not None and page > max_pages:
            break
        yield urljoin(base_url, f"{page}.shtml")
        page += 1


def _extract_article_links(html: bytes, page_url: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: List[str] = []
    seen: Set[str] = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not href:
            continue
        href = href.strip()
        if not href or href.startswith("javascript"):
            continue
        absolute = urljoin(page_url, href)
        if (
            "qianlong.com" not in absolute
            or not absolute.lower().endswith(".shtml")
            or "/20" not in absolute
        ):
            continue
        if absolute not in seen:
            seen.add(absolute)
            links.append(absolute)
    return links


def _extract_publish_time(html_text: str) -> Tuple[Optional[int], Optional[datetime], Optional[str]]:
    match = PUBLISH_TIME_PATTERN.search(html_text)
    if not match:
        return None, None, None
    raw = match.group(0).replace("\xa0", " ").strip()
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    except ValueError:
        return None, None, raw
    localized = parsed.replace(tzinfo=CHINA_TZ)
    timestamp = int(localized.astimezone(timezone.utc).timestamp())
    return timestamp, localized, raw


def _render_content_node(node: Tag | NavigableString, base_url: str) -> str:
    if isinstance(node, NavigableString):
        return str(node)
    if node.name == "br":
        return "\n"
    if node.name == "img":
        src = (node.get("src") or "").strip()
        if not src or src.startswith("data:"):
            return ""
        absolute = urljoin(base_url, src)
        alt = (node.get("alt") or "").strip()
        return f"![{alt}]({absolute})"
    return "".join(_render_content_node(child, base_url) for child in node.children)


def _markdown_from_content(content: Tag, base_url: str) -> str:
    for unwanted in content.select("script, style, noscript"):
        unwanted.decompose()

    chunks: List[str] = []

    for block in content.children:
        if isinstance(block, NavigableString):
            text = str(block).strip()
            if text:
                chunks.append(text)
        elif isinstance(block, Tag):
            rendered = _render_content_node(block, base_url).strip()
            if rendered:
                chunks.append(rendered)

    markdown = "\n\n".join(line.strip() for line in chunks if line.strip())
    return markdown

def _fetch_listing_response(session: requests.Session, page_url: str) -> requests.Response:
    resp = session.get(page_url)
    resp.raise_for_status()
    return resp

def _parse_listing_response(
    resp_content: bytes, 
    page_url: str, 
    seen: Set[str], 
    existing_ids: Optional[Set[str]], 
    consecutive_stop: Optional[int], 
    consecutive_hits: int
) -> Tuple[List[str], int, int]:
    collected: List[str] = []
    page_added = 0
    
    for link in _extract_article_links(resp_content, page_url):
        if link in seen:
            continue
        seen.add(link)
        article_id = make_article_id(link)
        if existing_ids and article_id in existing_ids:
            consecutive_hits += 1
            if consecutive_stop and consecutive_stop > 0 and consecutive_hits >= consecutive_stop:
                LOGGER.info(
                    "Qianlong consecutive existing articles reached %s, stopping listing crawl.",
                    consecutive_stop,
                )
                return collected, page_added, consecutive_hits
            continue
        
        consecutive_hits = 0
        collected.append(link)
        page_added += 1
        
    return collected, page_added, consecutive_hits


def _collect_article_urls(
    session: requests.Session,
    *,
    base_url: str,
    max_pages: Optional[int],
    limit: Optional[int],
    existing_ids: Optional[Set[str]],
    consecutive_stop: Optional[int],
) -> List[str]:
    collected: List[str] = []
    seen: Set[str] = set()
    consecutive_hits = 0
    consecutive_empty_pages = 0
    
    for page_index, page_url in enumerate(_iter_listing_urls(base_url, max_pages), start=1):
        try:
            resp = _fetch_listing_response(session, page_url)
        except Exception as exc:
            LOGGER.warning("Failed to fetch Qianlong listing %s: %s", page_url, exc)
            continue
            
        links, page_added, consecutive_hits = _parse_listing_response(
            resp.content, page_url, seen, existing_ids, consecutive_stop, consecutive_hits
        )
        
        collected.extend(links)
        
        # Check stop conditions
        if consecutive_stop and consecutive_stop > 0 and consecutive_hits >= consecutive_stop:
            return collected
        
        if limit is not None and len(collected) >= limit:
            return collected

        if page_added == 0:
            consecutive_empty_pages += 1
            if max_pages is None and consecutive_empty_pages >= 3:
                 LOGGER.info(
                    "No new Qianlong articles after %s listing pages; stopping listing crawl.",
                    consecutive_empty_pages,
                )
                 break
        else:
            consecutive_empty_pages = 0
            
    return collected


def _resolve_base_urls(
    base_url: Optional[str],
    base_urls: Optional[Sequence[str]],
) -> List[str]:
    candidates: Sequence[str]
    if base_urls is not None:
        candidates = base_urls
    elif base_url:
        candidates = (base_url,)
    else:
        candidates = DEFAULT_BASE_URLS

    resolved: List[str] = []
    seen: Set[str] = set()
    for candidate in candidates:
        normalized = str(candidate or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return resolved or list(DEFAULT_BASE_URLS)


def _merge_article_url_groups(
    groups: Sequence[Sequence[str]],
    limit: Optional[int],
) -> List[str]:
    merged: List[str] = []
    seen: Set[str] = set()
    max_group_size = max((len(group) for group in groups), default=0)
    for index in range(max_group_size):
        for group in groups:
            if index >= len(group):
                continue
            url = group[index]
            if url in seen:
                continue
            seen.add(url)
            merged.append(url)
            if limit is not None and len(merged) >= limit:
                return merged
    return merged


def _fetch_article_html(session: requests.Session, url: str) -> requests.Response:
    LOGGER.info("Fetching Qianlong article: %s", url)
    response = session.get(url)
    response.raise_for_status()
    return response

def _parse_article_html(html_content: bytes, html_text: str, url: str) -> Optional[QianlongArticle]:
    soup = BeautifulSoup(html_content, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url

    content_tag = (
        soup.select_one("#contentStr")
        or soup.select_one(".article-content")
        or soup.select_one(".content")
        or soup.select_one("article")
    )
    if not content_tag:
        LOGGER.warning("Content container missing for %s", url)
        return None

    publish_ts, publish_dt, raw_publish = _extract_publish_time(html_text)
    markdown = _markdown_from_content(content_tag, url)
    if not markdown:
        LOGGER.warning("Empty markdown extracted for %s", url)
        return None

    return QianlongArticle(
        title=title,
        url=url,
        publish_time=publish_ts,
        publish_time_iso=publish_dt,
        content_markdown=markdown,
        raw_publish_text=raw_publish,
    )

def _extract_article(session: requests.Session, url: str) -> Optional[QianlongArticle]:
    response = _fetch_article_html(session, url)
    return _parse_article_html(response.content, response.text, url)


def fetch_article(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> Optional[QianlongArticle]:
    session = _create_session(timeout)
    try:
        return _extract_article(session, url)
    finally:
        session.close()


def fetch_articles(
    limit: Optional[int] = None,
    *,
    base_url: Optional[str] = None,
    base_urls: Optional[Sequence[str]] = None,
    pages: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
    delay: float = DEFAULT_DELAY,
    existing_ids: Optional[Set[str]] = None,
    consecutive_stop: Optional[int] = None,
    entries: Optional[Sequence[ChannelEntry]] = None,
) -> List[QianlongArticle]:
    """Crawl configured 千龙网 channels following the shared adapter contract."""
    max_pages = DEFAULT_MAX_PAGES
    if pages is not None:
        try:
            candidate = int(pages)
        except Exception:
            candidate = None
        if candidate is not None and candidate > 0:
            max_pages = candidate
    session = _create_session(timeout)
    try:
        # 栏目清单以控制台 crawl_accounts 下发的 entries 为准；未提供时
        # （直连调用、测试、补录脚本）回退到 base_urls/环境变量/内置默认。
        if entries is None:
            listing_urls: List[str] = _resolve_base_urls(base_url, base_urls)
        else:
            listing_urls = []
            seen_entries: Set[str] = set()
            for entry in entries:
                if entry.url in seen_entries:
                    continue
                seen_entries.add(entry.url)
                listing_urls.append(entry.url)
        url_groups = (
            _collect_article_urls(
                session,
                base_url=listing_base_url,
                max_pages=max_pages,
                limit=limit,
                existing_ids=existing_ids,
                consecutive_stop=consecutive_stop,
            )
            for listing_base_url in listing_urls
        )
        urls = _merge_article_url_groups(list(url_groups), limit)
    finally:
        session.close()

    articles: List[QianlongArticle] = []
    session = _create_session(timeout)
    try:
        for idx, url in enumerate(urls, start=1):
            if limit is not None and len(articles) >= limit:
                break
            try:
                article = _extract_article(session, url)
            except requests.RequestException as exc:
                LOGGER.warning("Failed to fetch Qianlong article %s: %s", url, exc)
                continue
            if not article:
                continue
            articles.append(article)
            if delay > 0 and idx < len(urls):
                time.sleep(delay)
    finally:
        session.close()
    return articles


def make_article_id(url: str) -> str:
    parsed = urlparse((url or "").strip())
    path = parsed.path or "/"
    path = re.sub(r"\.s?html?$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/+", "/", path).strip("/")
    if not path:
        path = "index"
    return f"qianlong:{path}"


def article_to_feed_row(article: QianlongArticle, article_id: str, *, fetched_at: datetime) -> dict:
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


def article_to_detail_row(article: QianlongArticle, article_id: str, *, detail_fetched_at: datetime) -> dict:
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


__all__ = [
    "QianlongArticle",
    "ChannelEntry",
    "fetch_article",
    "fetch_articles",
    "make_article_id",
    "parse_channel_input",
    "article_to_feed_row",
    "article_to_detail_row",
    "SOURCE_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_EDU_BASE_URL",
    "DEFAULT_BASE_URLS",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_TIMEOUT",
]
