from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup, Comment, Tag


BASE_URL = "https://ldwb.workerbj.cn/"
SOURCE_NAME = "劳动午报"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    )
}
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_TIMEOUT = 20.0
DEFAULT_VERIFY_TLS = False
DEFAULT_PUBLISH_HOUR = 0


@dataclass
class ArticleRecord:
    article_id: str
    title: str
    url: str
    publish_date: str
    content_markdown: str
    page_name: str

    def publish_datetime(self) -> Optional[datetime]:
        if not self.publish_date:
            return None
        try:
            base = datetime.strptime(self.publish_date, "%Y-%m-%d")
            return base.replace(hour=DEFAULT_PUBLISH_HOUR, tzinfo=CHINA_TZ)
        except ValueError:
            return None


def _resolve_timeout(override: Optional[float]) -> float:
    if override is not None:
        return float(override)
    env_value = os.getenv("LDWB_TIMEOUT")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            return DEFAULT_TIMEOUT
    return DEFAULT_TIMEOUT


def _resolve_verify_tls(override: Optional[bool]) -> bool:
    if override is not None:
        return override
    env_value = os.getenv("LDWB_VERIFY_TLS")
    if env_value:
        normalized = env_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return DEFAULT_VERIFY_TLS


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update(DEFAULT_HEADERS)
    sess.trust_env = False
    return sess


def _fetch_text(session: requests.Session, url: str, *, timeout: float, verify: bool) -> str:
    resp = session.get(url, timeout=timeout, verify=verify)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _latest_issue_url(session: requests.Session, *, timeout: float, verify: bool) -> str:
    html_text = _fetch_text(session, BASE_URL, timeout=timeout, verify=verify)
    match = re.search(r'URL=([^"\']+)', html_text)
    if not match:
        raise RuntimeError("Failed to locate redirect URL for the latest issue.")
    return urljoin(BASE_URL, match.group(1))


def _extract_page_links(issue_html: str, issue_url: str) -> List[Tuple[str, str]]:
    soup = BeautifulSoup(issue_html, "html.parser")
    links: List[Tuple[str, str]] = []
    seen = set()
    for anchor in soup.select("a#pageLink"):
        href = anchor.get("href")
        if not href:
            continue
        full_url = urljoin(issue_url, href)
        if full_url in seen:
            continue
        text = " ".join(anchor.get_text(strip=True).split())
        links.append((text or full_url, full_url))
        seen.add(full_url)
    if not links:
        links.append(("默认版面", issue_url))
    return links


def _extract_article_links(page_html: str, page_url: str) -> List[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    links: List[str] = []
    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        lowered = href.lower()
        if "content_" not in lowered or not lowered.endswith(".htm"):
            continue
        full_url = urljoin(page_url, href)
        if full_url in seen:
            continue
        seen.add(full_url)
        links.append(full_url)
    return links


def _extract_enp_property(raw_html: str, field: str) -> str:
    pattern = rf"<founder-{field}>(.*?)</founder-{field}>"
    match = re.search(pattern, raw_html, flags=re.IGNORECASE | re.DOTALL)
    return html.unescape(match.group(1)).strip() if match else ""


def _guess_publish_date(article_url: str, fallback: str = "") -> str:
    match = re.search(r"/(\d{4}-\d{2})/(\d{2})/", article_url)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return fallback


def _founder_content_to_markdown(container: Tag) -> str:
    paragraphs: List[str] = []
    for p in container.find_all("p"):
        text = _normalize_text(p.get_text(" ", strip=True))
        if text:
            paragraphs.append(text)
    if not paragraphs:
        text = _normalize_text(container.get_text("\n", strip=True))
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def parse_article(html_text: str, article_url: str, page_name: str) -> ArticleRecord:
    soup = BeautifulSoup(html_text, "html.parser")
    content_root = soup.find("founder-content")
    if not content_root:
        raise RuntimeError(f"Unable to find founder-content for {article_url}")
    content_markdown = _founder_content_to_markdown(content_root)

    metadata_comment = next(
        (node for node in soup.find_all(string=lambda t: isinstance(t, Comment)) if "enpproperty" in node),
        None,
    )
    metadata_block = metadata_comment or ""
    title = _extract_enp_property(metadata_block, "title")
    subtitle = _extract_enp_property(metadata_block, "subtitle")
    if subtitle:
        title = f"{title}｜{subtitle}" if title else subtitle
    if not title and soup.title:
        title = _normalize_text(soup.title.get_text(strip=True))
    publish_date = _extract_enp_property(metadata_block, "date")
    publish_date = publish_date or _guess_publish_date(article_url)

    return ArticleRecord(
        article_id=make_article_id(article_url),
        title=title or "",
        url=article_url,
        publish_date=publish_date,
        content_markdown=content_markdown,
        page_name=page_name,
    )


def crawl_latest_issue(
    limit: Optional[int] = None,
    *,
    session: Optional[requests.Session] = None,
    verify_tls: Optional[bool] = None,
    timeout: Optional[float] = None,
) -> List[ArticleRecord]:
    timeout_s = _resolve_timeout(timeout)
    verify = _resolve_verify_tls(verify_tls)
    if not verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    sess = session or _session()
    created_session = session is None
    try:
        latest_issue = _latest_issue_url(sess, timeout=timeout_s, verify=verify)
        issue_html = _fetch_text(sess, latest_issue, timeout=timeout_s, verify=verify)
        page_links = _extract_page_links(issue_html, latest_issue)

        records: List[ArticleRecord] = []
        seen_urls = set()
        for page_name, page_url in page_links:
            page_html = _fetch_text(sess, page_url, timeout=timeout_s, verify=verify)
            article_links = _extract_article_links(page_html, page_url)
            for article_url in article_links:
                if article_url in seen_urls:
                    continue
                detail_html = _fetch_text(sess, article_url, timeout=timeout_s, verify=verify)
                record = parse_article(detail_html, article_url, page_name)
                records.append(record)
                seen_urls.add(article_url)
                if limit and len(records) >= limit:
                    return records
        return records
    finally:
        if created_session:
            sess.close()


def make_article_id(url: str) -> str:
    if not url:
        return "laodongwubao:/"
    normalized = re.sub(r"^https?://[^/]+", "", url.strip())
    normalized = re.sub(r"\.s?html?$", "", normalized)
    normalized = re.sub(r"/+", "/", normalized).strip()
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    return f"laodongwubao:{normalized or '/'}"


def article_to_feed_row(record: ArticleRecord, *, fetched_at: datetime) -> Dict[str, object]:
    publish_dt = record.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp()) if publish_dt else None
    return {
        "token": None,
        "profile_url": None,
        "article_id": record.article_id,
        "title": record.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": record.url,
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "fetched_at": fetched_at,
    }


def article_to_detail_row(record: ArticleRecord, *, detail_fetched_at: datetime) -> Dict[str, object]:
    publish_dt = record.publish_datetime()
    publish_ts = int(publish_dt.astimezone(timezone.utc).timestamp()) if publish_dt else None
    return {
        "token": None,
        "profile_url": None,
        "article_id": record.article_id,
        "title": record.title,
        "source": SOURCE_NAME,
        "publish_time": publish_ts,
        "publish_time_iso": publish_dt,
        "url": record.url,
        "summary": None,
        "comment_count": None,
        "digg_count": None,
        "content_markdown": record.content_markdown,
        "detail_fetched_at": detail_fetched_at,
    }


__all__ = [
    "ArticleRecord",
    "SOURCE_NAME",
    "crawl_latest_issue",
    "parse_article",
    "make_article_id",
    "article_to_feed_row",
    "article_to_detail_row",
]
