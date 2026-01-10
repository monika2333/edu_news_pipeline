from __future__ import annotations

import re
import json
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import os
from urllib.parse import urljoin
import time

import requests
from bs4 import BeautifulSoup


# --- HTTP/session helpers ---
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0 Safari/537.36"
)
CHINA_TZ = timezone(timedelta(hours=8))
DEFAULT_START_URL = (
    "https://cn.chinadaily.com.cn/5b753f9fa310030f813cf408/"
    "5bd54dd6a3101a87ca8ff5f8/5bd54e59a3101a87ca8ff606"
)


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
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://cn.chinadaily.com.cn/",
    })
    return s


def _response_text(resp: requests.Response) -> str:
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


def absolute_url(base_url: str, link: Optional[str]) -> Optional[str]:
    if not link:
        return None
    link = link.strip()
    if not link or link.lower().startswith("javascript"):
        return None
    return urljoin(base_url, link)


def normalize_url(url: str) -> str:
    return (url or "").strip()


def make_article_id(url: str) -> str:
    u = normalize_url(url)
    path = re.sub(r"^https?://[^/]+", "", u)
    path = re.sub(r"\.s?html?$", "", path)
    path = re.sub(r"/+", "/", path).strip()
    if not path:
        path = "/"
    return f"chinadaily:{path}"


def html_to_markdown(html_str: str) -> str:
    # A simple and robust conversion similar to ChinaNews adapter
    text = re.sub(r"<(?:/)?p[^>]*>", "\n\n", html_str or "", flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _dt_from_iso(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


# --- Listing page parsing ---
DATE_PATTERN = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{2}):(\d{2}))?")


def _extract_publish_iso(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = DATE_PATTERN.search(text)
    if not m:
        return None
    y, M, d, hh, mm = m.groups()
    try:
        hh_i = int(hh) if hh is not None else 0
        mm_i = int(mm) if mm is not None else 0
        dt = datetime(int(y), int(M), int(d), hh_i, mm_i, tzinfo=CHINA_TZ)
        return dt.isoformat()
    except Exception:
        return None

def _fetch_listing_html(session: requests.Session, page_url: str, timeout: float) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = session.get(page_url, timeout=timeout)
            resp.raise_for_status()
            return _response_text(resp)
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5 * (2 ** attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError("empty listing response")

def _parse_listing_page(
    html: str, 
    page_url: str,
    existing_ids: Optional[Set[str]],
    consecutive_stop: int,
    consecutive_hits: int
) -> Tuple[List[FeedItemLike], Optional[str], int]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[FeedItemLike] = []
    
    # Extract items
    extracted_items = []
    for h3 in soup.select("div.left-liebiao h3"):
        a = h3.find("a", href=True)
        if not a:
            continue
        href = absolute_url(page_url, a["href"]) or ""
        if not href:
            continue
        title = (a.get_text() or "").strip()
        publish_iso: Optional[str] = None
        # Neighbour paragraph under bus box often contains time
        container = h3.find_parent(class_=re.compile(r"busBox", re.I)) or h3.parent
        p = container.find("p") if container else None
        if p:
            strong = p.find("b")
            if strong and strong.get_text(strip=True):
                publish_iso = _extract_publish_iso(strong.get_text(strip=True))
            if not publish_iso:
                publish_iso = _extract_publish_iso(p.get_text(" ", strip=True))

        extracted_items.append(FeedItemLike(title=title, url=href, section=None, publish_time_iso=publish_iso, raw={}))

    # Filter loop
    for it in extracted_items:
        aid = make_article_id(it.url)
        if existing_ids is not None and aid in existing_ids:
            if consecutive_stop == 0:
                 continue
            consecutive_hits += 1
            if consecutive_hits >= consecutive_stop:
                # Early stop
                return items, None, consecutive_hits
            continue
        else:
            consecutive_hits = 0
        items.append(it)

    next_page_url: Optional[str] = None
    for anchor in soup.select("a.pagestyle[href]"):
        t = anchor.get_text(strip=True)
        if t and ("下一" in t or "下一页" in t):
            next_page_url = absolute_url(page_url, anchor["href"]) or None
            if next_page_url:
                break

    return items, next_page_url, consecutive_hits


def list_items(limit: Optional[int] = None, pages: Optional[int] = None, *, existing_ids: Optional[Set[str]] = None) -> List[FeedItemLike]:
    sess = _session()
    try:
        start_url = os.getenv("CHINADAILY_START_URL") or DEFAULT_START_URL
    except Exception:
        start_url = DEFAULT_START_URL
    try:
        consecutive_stop = int(os.getenv("CHINADAILY_EXISTING_CONSECUTIVE_STOP", "5"))
    except Exception:
        consecutive_stop = 5
    if consecutive_stop < 0:
        consecutive_stop = 0
    try:
        timeout = float(os.getenv("CHINADAILY_TIMEOUT", "20"))
    except Exception:
        timeout = 20.0

    collected: List[FeedItemLike] = []
    page_url: Optional[str] = start_url
    page_idx = 0
    consecutive_hits = 0

    max_pages = max(1, int(pages or 1))

    while page_url and page_idx < max_pages:
        page_idx += 1
        
        try:
            html = _fetch_listing_html(sess, page_url, timeout)
        except Exception:
             # simple retry or break handled inside _fetch or here
             break
        
        items, next_page, consecutive_hits = _parse_listing_page(
            html, page_url, existing_ids, consecutive_stop, consecutive_hits
        )
        
        collected.extend(items)
        if limit is not None and len(collected) >= limit:
            collected = collected[:limit]
            break
            
        if consecutive_stop > 0 and consecutive_hits >= consecutive_stop:
            break
            
        page_url = next_page
        
    return collected


# --- Detail page parsing ---
def _get_meta(soup: BeautifulSoup, *, names: Sequence[str] = (), props: Sequence[str] = ()) -> Optional[str]:
    for n in names:
        el = soup.find("meta", attrs={"name": n})
        if el and el.get("content"):
            return (el.get("content") or "").strip()
    for p in props:
        el = soup.find("meta", attrs={"property": p})
        if el and el.get("content"):
            return (el.get("content") or "").strip()
    return None

def _fetch_detail_html(session: requests.Session, url: str, timeout: float) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = session.get(normalize_url(url), timeout=timeout)
            resp.raise_for_status()
            return _response_text(resp)
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5 * (2 ** attempt))
    if last_exc:
        raise last_exc
    raise RuntimeError("empty detail response")

def _parse_detail_html(html_text: str, url: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")

    title = None
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(strip=True)
    if not title:
        title = _get_meta(soup, props=["og:title"]) or _get_meta(soup, names=["twitter:title", "headline"]) or (soup.title.string.strip() if soup.title and soup.title.string else None)

    # Publish time via meta or visible time blocks
    publish_iso = _get_meta(soup, names=[
        "publishdate", "PubDate", "pubdate", "publish_time"
    ], props=["article:published_time", "og:pubdate"]) or None
    if not publish_iso:
        time_node = soup.find(class_=re.compile(r"date|datetxt|show_Date|time|data", re.I))
        if time_node and time_node.get_text(strip=True):
            publish_iso = _extract_publish_iso(time_node.get_text(" ", strip=True))

    # Content container heuristics
    content_node = None
    for sel in ("#Content", ".content", ".contentMain", ".main-content", ".main_artic", ".main-artic", ".article", ".article-left-new", ".articleContent", ".article-content", ".left_zw", ".TRS_Editor", "article"):
        content_node = soup.select_one(sel)
        if content_node and content_node.get_text(strip=True):
            break
        content_node = None
    if not content_node:
        for div in soup.find_all("div"):
            ident = " ".join(filter(None, [div.get("id", ""), " ".join(div.get("class", []))])).lower()
            if ("content" in ident or "article" in ident) and div.get_text(strip=True):
                content_node = div
                break

    if content_node:
        for bad in content_node.select("script, style, .ad, .adEditor, .adInContent"):
            bad.decompose()
        content_html = content_node.decode_contents()
    else:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        content_html = (meta_desc.get("content") if meta_desc and meta_desc.get("content") else html_text)

    text_md = html_to_markdown(content_html)

    return {
        "title": title.strip() if title else None,
        "source": "中国日报",
        "publish_time": None,
        "publish_time_iso": publish_iso,
        "url": normalize_url(url),
        "content": content_html,
        "content_markdown": text_md,
    }


def fetch_detail(url: str) -> Dict[str, Any]:
    sess = _session()
    timeout = float(os.getenv("CHINADAILY_TIMEOUT", "20"))
    html = _fetch_detail_html(sess, url, timeout)
    return _parse_detail_html(html, url)


def feed_item_to_row(item: FeedItemLike, article_id: str, *, fetched_at: datetime) -> Dict[str, Any]:
    dt = _dt_from_iso(item.publish_time_iso)
    ts = int(dt.astimezone(timezone.utc).timestamp()) if dt else None
    return {
        'token': None,
        'profile_url': None,
        'article_id': article_id,
        'title': item.title,
        'source': item.section,
        'publish_time': ts,
        'publish_time_iso': dt,
        'url': item.url,
        'summary': None,
        'comment_count': None,
        'digg_count': None,
        'fetched_at': fetched_at,
    }


def build_detail_update(item: FeedItemLike, article_id: str, data: Dict[str, Any], *, detail_fetched_at: datetime) -> Dict[str, Any]:
    pub_iso = data.get('publish_time_iso') or item.publish_time_iso
    pub_dt = _dt_from_iso(pub_iso)
    pub_ts = data.get('publish_time')
    if pub_ts is None and pub_dt is not None:
        pub_ts = int(pub_dt.astimezone(timezone.utc).timestamp())
    return {
        'token': None,
        'profile_url': None,
        'article_id': article_id,
        'title': (data.get('title') or item.title or '').strip(),
        'source': (data.get('source') or item.section or '中国日报').strip(),
        'publish_time': pub_ts,
        'publish_time_iso': pub_dt,
        'url': data.get('url') or item.url,
        'summary': None,
        'comment_count': None,
        'digg_count': None,
        'content_markdown': data.get('content_markdown') or html_to_markdown(data.get('content') or ''),
        'detail_fetched_at': detail_fetched_at,
    }


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
