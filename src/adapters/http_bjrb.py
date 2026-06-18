from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag  # type: ignore

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://bjrbdzb.bjd.com.cn/bjrb"
SOURCE_NAME = "北京日报"
SOURCE_KEY = "bjrb"
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT = 20.0
DEFAULT_DELAY = 0.2
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/129.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


@dataclass(slots=True)
class BjrbIssueItem:
    article_id: str
    title: str
    url: str
    publish_date: str
    page_name: str
    newid: Optional[str]

    def publish_datetime(self) -> datetime:
        base = datetime.strptime(self.publish_date, "%Y%m%d")
        return base.replace(tzinfo=CHINA_TZ)


@dataclass(slots=True)
class BjrbArticle:
    article_id: str
    title: str
    url: str
    publish_date: str
    content_markdown: str
    page_name: str
    guide: Optional[str]
    subtitle: Optional[str]
    newid: Optional[str]

    def publish_datetime(self) -> datetime:
        base = datetime.strptime(self.publish_date, "%Y%m%d")
        return base.replace(tzinfo=CHINA_TZ)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _normalize_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.getenv("BJRB_BASE_URL") or BASE_URL).rstrip("/")


def _resolve_timeout(timeout: Optional[float]) -> float:
    return float(timeout) if timeout is not None else _env_float("BJRB_TIMEOUT", DEFAULT_TIMEOUT)


def _resolve_delay(delay: Optional[float]) -> float:
    resolved = float(delay) if delay is not None else _env_float("BJRB_DELAY", DEFAULT_DELAY)
    return max(0.0, resolved)


def resolve_issue_date(value: Optional[str] = None) -> str:
    raw = (value or os.getenv("BJRB_DATE") or "").strip()
    if not raw:
        return datetime.now(CHINA_TZ).strftime("%Y%m%d")
    if not re.fullmatch(r"\d{8}", raw):
        raise ValueError("BJRB_DATE must use YYYYMMDD format")
    datetime.strptime(raw, "%Y%m%d")
    return raw


def _issue_index_url(issue_date: str, *, base_url: Optional[str] = None) -> str:
    root = _normalize_base_url(base_url)
    year = issue_date[:4]
    return f"{root}/mobile/{year}/{issue_date}/{issue_date}_m.html"


def _period_url(issue_date: str, *, base_url: Optional[str] = None) -> str:
    root = _normalize_base_url(base_url)
    return f"{root}/period/{issue_date[:6]}/period.js"


def _create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def _decode_html(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _fetch_text(session: requests.Session, url: str, *, timeout: float) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return _decode_html(response.content)


def parse_period_dates(period_js: str) -> list[str]:
    return re.findall(r"\d{8}", period_js or "")


def _issue_date_available(
    issue_date: str,
    *,
    session: requests.Session,
    base_url: Optional[str],
    timeout: float,
) -> bool:
    url = _period_url(issue_date, base_url=base_url)
    try:
        text = _fetch_text(session, url, timeout=timeout)
    except requests.RequestException as exc:
        LOGGER.warning("Failed to fetch BJRB period index %s: %s", url, exc)
        return False
    return issue_date in parse_period_dates(text)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def make_article_id(url: str, newid: Optional[str] = None) -> str:
    cleaned_newid = (newid or "").strip()
    if cleaned_newid:
        return f"{SOURCE_KEY}:{cleaned_newid}"
    parsed = urlparse(urldefrag((url or "").strip()).url)
    path = re.sub(r"\.s?html?$", "", parsed.path or "", flags=re.IGNORECASE)
    path = re.sub(r"/+", "/", path).strip("/")
    return f"{SOURCE_KEY}:{path or 'index'}"


def parse_issue_index(html_text: str, issue_url: str, issue_date: str) -> list[BjrbIssueItem]:
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[BjrbIssueItem] = []
    seen_ids: set[str] = set()

    for nav_item in soup.select("div.nav-items"):
        heading = nav_item.select_one("div.nav-panel-heading")
        page_name = _clean_text(heading.get_text(" ", strip=True)) if heading else ""
        for anchor in nav_item.select("a[data-href]"):
            href = (anchor.get("data-href") or "").strip()
            if not href:
                continue
            url = urldefrag(urljoin(issue_url, href)).url
            newid = (anchor.get("data-newid") or "").strip() or None
            article_id = make_article_id(url, newid)
            if article_id in seen_ids:
                continue
            seen_ids.add(article_id)
            title = _clean_text(anchor.get_text(" ", strip=True))
            items.append(
                BjrbIssueItem(
                    article_id=article_id,
                    title=title,
                    url=url,
                    publish_date=issue_date,
                    page_name=page_name,
                    newid=newid,
                )
            )
    return items


def list_issue_items(
    *,
    issue_date: Optional[str] = None,
    limit: Optional[int] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
    session: Optional[requests.Session] = None,
) -> list[BjrbIssueItem]:
    date_value = resolve_issue_date(issue_date)
    timeout_s = _resolve_timeout(timeout)
    sess = session or _create_session()
    created_session = session is None
    try:
        if not _issue_date_available(date_value, session=sess, base_url=base_url, timeout=timeout_s):
            LOGGER.info("BJRB issue %s is not listed in period index.", date_value)
            return []
        issue_url = _issue_index_url(date_value, base_url=base_url)
        html_text = _fetch_text(sess, issue_url, timeout=timeout_s)
        items = parse_issue_index(html_text, issue_url, date_value)
        if limit is not None and limit > 0:
            return items[:limit]
        return items
    finally:
        if created_session:
            sess.close()


def _extract_optional_text(soup: BeautifulSoup, selector: str) -> Optional[str]:
    node = soup.select_one(selector)
    if node is None:
        return None
    text = _clean_text(node.get_text(" ", strip=True))
    return text or None


def _render_image(node: Tag, base_url: str) -> Optional[str]:
    src = (node.get("src") or "").strip()
    if not src or src.startswith("data:"):
        return None
    alt = _clean_text(node.get("alt") or "")
    return f"![{alt}]({urljoin(base_url, src)})"


def _content_to_markdown(content: Tag, base_url: str) -> str:
    chunks: list[str] = []
    for unwanted in content.select("script, style, noscript"):
        unwanted.decompose()

    paragraphs = content.find_all("p")
    if paragraphs:
        for paragraph in paragraphs:
            text = _clean_text(paragraph.get_text(" ", strip=True))
            if text:
                chunks.append(text)
            for image in paragraph.find_all("img"):
                rendered = _render_image(image, base_url)
                if rendered:
                    chunks.append(rendered)
    else:
        text = _clean_text(content.get_text("\n", strip=True))
        if text:
            chunks.append(text)
        for image in content.find_all("img"):
            rendered = _render_image(image, base_url)
            if rendered:
                chunks.append(rendered)
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _date_from_display(value: Optional[str], fallback: str) -> str:
    if not value:
        return fallback
    match = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", value)
    if not match:
        return fallback
    year, month, day = match.groups()
    return f"{int(year):04d}{int(month):02d}{int(day):02d}"


def parse_article(html_text: str, item: BjrbIssueItem) -> BjrbArticle:
    soup = BeautifulSoup(html_text, "html.parser")
    guide = _extract_optional_text(soup, "#guide")
    title = _extract_optional_text(soup, "#main-title") or item.title
    subtitle = _extract_optional_text(soup, "#sub-title")
    date_text = _extract_optional_text(soup, "#date")
    publish_date = _date_from_display(date_text, item.publish_date)
    content = soup.select_one("#content")
    if content is None:
        raise RuntimeError(f"Unable to find BJRB article content for {item.url}")
    content_markdown = _content_to_markdown(content, item.url)
    if not content_markdown:
        raise RuntimeError(f"Empty BJRB article content for {item.url}")
    return BjrbArticle(
        article_id=item.article_id,
        title=title,
        url=item.url,
        publish_date=publish_date,
        content_markdown=content_markdown,
        page_name=item.page_name,
        guide=guide,
        subtitle=subtitle,
        newid=item.newid,
    )


def fetch_article(
    item: BjrbIssueItem,
    *,
    timeout: Optional[float] = None,
    session: Optional[requests.Session] = None,
) -> BjrbArticle:
    timeout_s = _resolve_timeout(timeout)
    sess = session or _create_session()
    created_session = session is None
    try:
        html_text = _fetch_text(sess, item.url, timeout=timeout_s)
        return parse_article(html_text, item)
    finally:
        if created_session:
            sess.close()


def crawl_issue(
    *,
    issue_date: Optional[str] = None,
    limit: Optional[int] = None,
    base_url: Optional[str] = None,
    timeout: Optional[float] = None,
    delay: Optional[float] = None,
) -> list[BjrbArticle]:
    timeout_s = _resolve_timeout(timeout)
    delay_s = _resolve_delay(delay)
    session = _create_session()
    try:
        items = list_issue_items(
            issue_date=issue_date,
            limit=limit,
            base_url=base_url,
            timeout=timeout_s,
            session=session,
        )
        articles: list[BjrbArticle] = []
        for index, item in enumerate(items, start=1):
            articles.append(fetch_article(item, timeout=timeout_s, session=session))
            if delay_s and index < len(items):
                time.sleep(delay_s)
        return articles
    finally:
        session.close()


def article_to_feed_row(item: BjrbIssueItem, *, fetched_at: datetime) -> dict[str, Any]:
    publish_dt = item.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp())
    return {
        "token": None,
        "profile_url": None,
        "article_id": item.article_id,
        "title": item.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": item.url,
        "summary": item.page_name or None,
        "comment_count": None,
        "digg_count": None,
        "fetched_at": fetched_at,
    }


def article_to_detail_row(article: BjrbArticle, *, detail_fetched_at: datetime) -> dict[str, Any]:
    publish_dt = article.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp())
    subtitle = article.subtitle or None
    summary_parts = [part for part in (article.page_name, article.guide, subtitle) if part]
    return {
        "token": None,
        "profile_url": None,
        "article_id": article.article_id,
        "title": article.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": article.url,
        "summary": "｜".join(summary_parts) if summary_parts else None,
        "comment_count": None,
        "digg_count": None,
        "content_markdown": article.content_markdown,
        "detail_fetched_at": detail_fetched_at,
    }


__all__ = [
    "BASE_URL",
    "BjrbArticle",
    "BjrbIssueItem",
    "DEFAULT_DELAY",
    "DEFAULT_TIMEOUT",
    "SOURCE_KEY",
    "SOURCE_NAME",
    "article_to_detail_row",
    "article_to_feed_row",
    "crawl_issue",
    "fetch_article",
    "list_issue_items",
    "make_article_id",
    "parse_article",
    "parse_issue_index",
    "parse_period_dates",
    "resolve_issue_date",
]
