from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag  # type: ignore
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://beijing.qianlong.com/"
DEFAULT_MAX_PAGES: Optional[int] = None
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


def _tag_text(tag: Tag) -> str:
    return tag.get_text(" ", strip=True)


def _markdown_from_content(content: Tag, base_url: str) -> str:
    for unwanted in content.select("script, style, noscript"):
        unwanted.decompose()

    chunks: List[str] = []

    def handle_paragraph(node: Tag) -> None:
        parts: List[str] = []
        for child in node.descendants:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                if child.name == "br":
                    parts.append("\n")
                elif child.name == "img":
                    src = child.get("src")
                    if not src:
                        continue
                    absolute = urljoin(base_url, src)
                    alt = (child.get("alt") or "").strip()
                    parts.append(f"![{alt}]({absolute})")
                else:
                    text = _tag_text(child)
                    if text:
                        parts.append(text)
        joined = "".join(parts).strip()
        if joined:
            chunks.append(joined)

    for block in content.children:
        if isinstance(block, NavigableString):
            text = str(block).strip()
            if text:
                chunks.append(text)
        elif isinstance(block, Tag):
            if block.name in {"p", "div", "section"}:
                handle_paragraph(block)
            elif block.name == "img":
                src = block.get("src")
                if not src:
                    continue
                absolute = urljoin(base_url, src)
                alt = (block.get("alt") or "").strip()
                chunks.append(f"![{alt}]({absolute})")
            else:
                text = _tag_text(block)
                if text:
                    chunks.append(text)

    markdown = "\n\n".join(line.strip() for line in chunks if line.strip())
    return markdown


def _extract_article(session: requests.Session, url: str) -> Optional[QianlongArticle]:
    LOGGER.info("Fetching Qianlong article: %s", url)
    response = session.get(url)
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else url

    content_tag = soup.select_one("#contentStr, .article-content, .content, article")
    if not content_tag:
        LOGGER.warning("Content container missing for %s", url)
        return None

    publish_ts, publish_dt, raw_publish = _extract_publish_time(response.text)
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
            resp = session.get(page_url)
            resp.raise_for_status()
        except Exception as exc:
            LOGGER.warning("Failed to fetch Qianlong listing %s: %s", page_url, exc)
            continue
        page_added = 0
        for link in _extract_article_links(resp.content, page_url):
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
                    return collected
                continue
            consecutive_hits = 0
            collected.append(link)
            page_added += 1
            if limit is not None and len(collected) >= limit:
                return collected
        if limit is not None and len(collected) >= limit:
            break
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


def fetch_articles(
    limit: Optional[int] = None,
    *,
    base_url: str = DEFAULT_BASE_URL,
    pages: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
    delay: float = DEFAULT_DELAY,
    existing_ids: Optional[Set[str]] = None,
    consecutive_stop: Optional[int] = None,
) -> List[QianlongArticle]:
    """Crawl 千龙网 articles following the shared adapter contract."""
    max_pages = None
    if pages is not None:
        try:
            candidate = int(pages)
        except Exception:
            candidate = None
        if candidate is not None and candidate > 0:
            max_pages = candidate
    elif DEFAULT_MAX_PAGES is not None:
        max_pages = DEFAULT_MAX_PAGES
    session = _create_session(timeout)
    try:
        urls = _collect_article_urls(
            session,
            base_url=base_url,
            max_pages=max_pages,
            limit=limit,
            existing_ids=existing_ids,
            consecutive_stop=consecutive_stop,
        )
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
    "fetch_articles",
    "make_article_id",
    "article_to_feed_row",
    "article_to_detail_row",
    "SOURCE_NAME",
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_PAGES",
    "DEFAULT_TIMEOUT",
]
