from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import os

import requests
from bs4 import BeautifulSoup

from src.adapters.http_linked_page_rows import build_detail_update as build_linked_detail_update
from src.adapters.http_linked_page_rows import feed_item_to_row as linked_feed_item_to_row

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# China timezone for ChinaNews timestamps
CHINA_TZ = timezone(timedelta(hours=8))
SOURCE_NAME = "中国新闻网"


@dataclass
class FeedItemLike:
    title: str
    url: str
    section: Optional[str]
    publish_time_iso: Optional[str]
    raw: Dict[str, Any]


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return s


def _response_text(resp: requests.Response) -> str:
    # Prefer server-declared encoding; if missing or iso-8859-1, use apparent_encoding
    try:
        enc = (resp.encoding or "").lower()
    except Exception:
        enc = ""
    if not enc or enc == "iso-8859-1":
        try:
            apparent = resp.apparent_encoding or "utf-8"
            resp.encoding = apparent
        except Exception:
            resp.encoding = "utf-8"
    return resp.text or ""


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return "https://www.chinanews.com.cn" + u
    return u


def make_article_id(url: str) -> str:
    u = normalize_url(url)
    path = re.sub(r"^https?://[^/]+", "", u)
    path = re.sub(r"\.s?html?$", "", path)
    path = re.sub(r"/+", "/", path).strip()
    if not path:
        path = "/"
    return f"chinanews:{path}"


def html_to_markdown(html_str: str) -> str:
    text = re.sub(r"<(?:/)?p[^>]*>", "\n\n", html_str or "", flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _first(soup: BeautifulSoup, selectors: Sequence[str]) -> Optional[Any]:
    for sel in selectors:
        node = soup.select_one(sel)
        if node:
            return node
    return None


def _get_meta(soup: BeautifulSoup, *, names: Sequence[str] = (), props: Sequence[str] = (), itemprops: Sequence[str] = ()) -> Optional[str]:
    for n in names:
        el = soup.find("meta", attrs={"name": n})
        if el and el.get("content"):
            return (el.get("content") or "").strip()
    for p in props:
        el = soup.find("meta", attrs={"property": p})
        if el and el.get("content"):
            return (el.get("content") or "").strip()
    for ip in itemprops:
        el = soup.find("meta", attrs={"itemprop": ip})
        if el and el.get("content"):
            return (el.get("content") or "").strip()
    return None


def _parse_datetime_str(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    s = value.strip()
    # Common formats: 2025-10-07 12:34, 2025/10/07 12:34, 2025年10月07日 12:34, 2025-10-07T12:34:56Z
    patterns = [
        r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?",
        r"(\d{4})/(\d{1,2})/(\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?",
        r"(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})",
    ]
    for pat in patterns:
        m = re.search(pat, s)
        if m:
            y, M, d, h, mi, sec = m.groups() + (None,)*max(0, 6-len(m.groups()))
            try:
                h = h or "0"; mi = mi or "0"; sec = sec or "0"
                dt = datetime(int(y), int(M), int(d), int(h), int(mi), int(sec), tzinfo=CHINA_TZ)
                return dt
            except Exception:
                continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(CHINA_TZ)
    except Exception:
        return None


def _strip_site_suffix(title: str) -> str:
    s = title.strip()
    s = re.sub(r"[-|_]\s*中国新闻网.*$", "", s)
    s = re.sub(r"[-|_]\s*中新网.*$", "", s)
    return s.strip()


def _date_from_url(url: str) -> Optional[datetime]:
    u = normalize_url(url)
    m = re.search(r"/(\d{4})/(\d{1,2})-(\d{1,2})/", u)
    if not m:
        return None
    try:
        y, M, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return datetime(y, M, d, 0, 0, 0, tzinfo=CHINA_TZ)
    except Exception:
        return None


def _ts_from_iso(iso: Optional[str]) -> Optional[int]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.astimezone(timezone.utc).timestamp())
    except Exception:
        return None


def _fetch_page_html(page: int, sess: requests.Session) -> str:
    url = f"https://www.chinanews.com.cn/scroll-news/news{page}.html"
    resp = sess.get(url, timeout=15)
    resp.raise_for_status()
    return _response_text(resp)


def _extract_max_page(soup: BeautifulSoup) -> int:
    box = soup.select_one("div.pagebox")
    if not box:
        return 1
    pages_local: List[int] = []
    for node in box.find_all(["a", "span"]):
        text = node.get_text(strip=True)
        if text.isdigit():
            try:
                pages_local.append(int(text))
            except Exception:
                continue
    return max(pages_local) if pages_local else 1


def _parse_page_items(
    html_text: str, 
    existing_ids: Optional[Set[str]], 
    consecutive_stop: int,
    consecutive_hits: int
) -> Tuple[List[FeedItemLike], int, Optional[int]]:
    soup = BeautifulSoup(html_text, "html.parser")
    last_page = _extract_max_page(soup)
    collected: List[FeedItemLike] = []
    ul = soup.select_one(".content_list")
    
    if not ul:
        return collected, consecutive_hits, last_page

    for li in ul.select("li"):
        a = li.select_one(".dd_bt a") or li.select_one("a")
        if not a or not a.get("href"):
            continue
        href = normalize_url(a["href"]) 
        title = (a.get_text() or "").strip()
        tnode = li.select_one(".dd_time")
        # Build publish iso from URL date + time (e.g., 10-7 23:43)
        base_date = _date_from_url(href) or datetime.now(tz=CHINA_TZ)
        hh = mm = 0
        if tnode:
            text = (tnode.get_text() or "").strip()
            m = re.match(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", text)
            if m:
                try:
                    hh, mm = int(m.group(3)), int(m.group(4))
                except Exception:
                    hh = mm = 0
            else:
                m2 = re.search(r"(\d{1,2}):(\d{2})", text)
                if m2:
                    hh, mm = int(m2.group(1)), int(m2.group(2))
        publish_dt = base_date.replace(hour=hh, minute=mm, second=0, microsecond=0)
        iso: Optional[str] = publish_dt.isoformat()
        item = FeedItemLike(
            title=title,
            url=href,
            section=SOURCE_NAME,
            publish_time_iso=iso,
            raw={},
        )
        aid = make_article_id(href)
        if existing_ids is not None and aid in existing_ids:
            if consecutive_stop == 0:
                continue
            consecutive_hits += 1
            if consecutive_hits >= consecutive_stop:
                return collected, consecutive_hits, last_page
            continue
        else:
            consecutive_hits = 0
        collected.append(item)
    
    return collected, consecutive_hits, last_page


def list_items(limit: Optional[int] = None, pages: Optional[int] = None, *, existing_ids: Optional[Set[str]] = None) -> List[FeedItemLike]:
    sess = _session()
    collected: List[FeedItemLike] = []
    requested_pages = max(1, int(pages or 1))
    try:
        consecutive_stop = int(os.getenv("CHINANEWS_EXISTING_CONSECUTIVE_STOP", "5"))
    except Exception:
        consecutive_stop = 5
    if consecutive_stop < 0:
        consecutive_stop = 0
    consecutive_hits = 0

    page = 1
    last_page: Optional[int] = None
    
    while page <= requested_pages:
        if limit is not None and len(collected) >= limit:
            break
        
        try:
            html = _fetch_page_html(page, sess)
        except Exception:
             # simple retry or break
             break
        
        items, consecutive_hits, detected_max_page = _parse_page_items(html, existing_ids, consecutive_stop, consecutive_hits)
        
        if last_page is None and detected_max_page:
            last_page = detected_max_page
            requested_pages = min(requested_pages, last_page)
            
        collected.extend(items)
        
        if consecutive_stop > 0 and consecutive_hits >= consecutive_stop:
            break
            
        page += 1
        
    if limit is not None:
        collected = collected[:limit]
    return collected


def _fetch_detail_html(url: str) -> str:
    sess = _session()
    resp = sess.get(normalize_url(url), timeout=15)
    resp.raise_for_status()
    return _response_text(resp)


def _parse_detail_html(html_text: str, url: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")

    # Title: h1 -> og:title -> <title> (strip site suffix)
    title = None
    h1 = _first(soup, ["h1", ".content h1", ".left_zw h1"]) 
    if h1:
        title = (h1.get_text() or "").strip()
    if not title:
        tmeta = _get_meta(soup, props=["og:title"]) or _get_meta(soup, names=["title"]) 
        if tmeta:
            title = tmeta.strip()
    if not title and soup.title and soup.title.string:
        title = _strip_site_suffix(str(soup.title.string))

    publish_iso = None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
            if isinstance(data, dict):
                publish_iso = data.get("datePublished") or data.get("dateModified")
                if publish_iso:
                    break
        except Exception:
            continue
    if not publish_iso:
        publish_iso = _get_meta(soup, names=["pubdate"], itemprops=["datePublished"], props=["article:published_time"]) or None

    if not publish_iso:
        time_line = None
        cand = _first(soup, [".left-time", ".time", ".news-time", ".content .time"]) or None
        if cand:
            time_line = cand.get_text(" ", strip=True)
        if time_line:
            if not publish_iso:
                dt = _parse_datetime_str(time_line)
                if dt:
                    publish_iso = dt.isoformat()

    # Content with multiple fallbacks
    content_node = None
    for selector in ("#p-detail", ".left_zw", "#content", "article", ".content"):
        content_node = soup.select_one(selector)
        if content_node and len(content_node.get_text(strip=True)) > 40:
            break
        content_node = None
    if content_node:
        # Remove scripts/styles/ads inside content
        for bad in content_node.select("script, style, .ad, .adEditor, .adInContent"):
            bad.decompose()
        content_html = content_node.decode_contents()
    else:
        # Fallback to meta description for video/special pages
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            content_html = meta_desc.get("content") or ""
        else:
            content_html = html_text
    text_md = html_to_markdown(content_html)

    # Per current requirement, prefer feed time; do not override from detail.
    publish_time_iso = None
    publish_time = None

    if title:
        title = title.strip()
    return {
        "title": title,
        "source": SOURCE_NAME,
        "publish_time": publish_time,
        "publish_time_iso": publish_time_iso,
        "url": normalize_url(url),
        "content": content_html,
        "content_markdown": text_md,
    }


def fetch_detail(url: str) -> Dict[str, Any]:
    html = _fetch_detail_html(url)
    return _parse_detail_html(html, url)


def feed_item_to_row(item: FeedItemLike, article_id: str, *, fetched_at: datetime) -> Dict[str, Any]:
    source_item = FeedItemLike(
        title=item.title,
        url=item.url,
        section=SOURCE_NAME,
        publish_time_iso=item.publish_time_iso,
        raw=item.raw,
    )
    return linked_feed_item_to_row(source_item, article_id, fetched_at=fetched_at)


def build_detail_update(item: FeedItemLike, article_id: str, data: Dict[str, Any], *, detail_fetched_at: datetime) -> Dict[str, Any]:
    return build_linked_detail_update(
        item,
        article_id,
        {**data, "source": SOURCE_NAME},
        detail_fetched_at=detail_fetched_at,
        default_source=SOURCE_NAME,
        render_content=html_to_markdown,
    )


__all__ = [
    'FeedItemLike',
    'normalize_url',
    'make_article_id',
    'list_items',
    'fetch_detail',
    'html_to_markdown',
    'feed_item_to_row',
    'build_detail_update',
]
