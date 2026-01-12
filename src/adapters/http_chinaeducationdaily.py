from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

BASE_URL = "http://www.jyb.cn"
SEARCH_URL = f"{BASE_URL}/search.html"
DEFAULT_SEARCH_API_URL = "http://new.jyb.cn/jybuc/hyBaseCol/search.action"
CHINA_TZ = timezone(timedelta(hours=8))


@dataclass
class FeedItemLike:
    title: str
    url: str
    section: Optional[str]
    publish_time_iso: Optional[str]
    raw: Dict[str, Any]


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Referer": BASE_URL,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Connection": "keep-alive",
        }
    )
    return s


def _response_text(resp: requests.Response) -> str:
    # Prefer apparent encoding to handle GBK-based pages
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
    return (url or "").strip()


def make_article_id(url: str) -> str:
    u = normalize_url(url)
    path = re.sub(r"^https?://[^/]+", "", u)
    path = re.sub(r"\.s?html?$", "", path)
    path = re.sub(r"/+", "/", path).strip()
    if not path:
        path = "/"
    return f"jyb:{path}"


def _extract_iso_from_text(text: str) -> Optional[str]:
    # Matches: YYYY-MM-DD or with time HH:MM[:SS]
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})(?:[\sT](\d{2}):(\d{2})(?::(\d{2}))?)?", text)
    if not m:
        return None
    y, M, d, hh, mm, ss = m.groups()
    try:
        hh_i = int(hh) if hh else 0
        mm_i = int(mm) if mm else 0
        ss_i = int(ss) if ss else 0
        dt = datetime(int(y), int(M), int(d), hh_i, mm_i, ss_i, tzinfo=CHINA_TZ)
        return dt.isoformat()
    except Exception:
        return None


def _dt_from_iso(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


def _request(sess: requests.Session, url: str, *, params: Optional[dict] = None, timeout: float = 15.0) -> requests.Response:
    headers = {"X-Forwarded-For": ".".join(str(random.randint(1, 255)) for _ in range(4))}
    resp = sess.get(url, params=params, timeout=timeout, headers=headers)
    resp.raise_for_status()
    return resp


def _parse_listing_html(html: str, page_url: str) -> List[FeedItemLike]:
    soup = BeautifulSoup(html, "html.parser")
    containers: Sequence = (
        soup.select(".res-list li")
        or soup.select(".search-result li")
        or soup.select(".list-left li")
        or soup.select(".clist li")
        or soup.find_all("li")
    )
    seen: Set[str] = set()
    items: List[FeedItemLike] = []
    for li in containers:
        a = li.find("a", href=True)
        if not a:
            continue
        href_raw = a["href"]
        if href_raw and href_raw.strip().lower().startswith("javascript:"):
            continue
        href = urljoin(page_url, href_raw) if not urlparse(href_raw).scheme else href_raw
        title = a.get_text(strip=True)
        if not href or not title or href in seen:
            continue
        seen.add(href)
        publish_iso = _extract_iso_from_text(li.get_text(" ", strip=True))
        items.append(FeedItemLike(title=title, url=href, section=None, publish_time_iso=publish_iso, raw={}))
    return items


def list_items(limit: Optional[int] = None, pages: Optional[int] = None, *, existing_ids: Optional[Set[str]] = None) -> List[FeedItemLike]:
    sess = _session()
    try:
        timeout = float(os.getenv("JYB_TIMEOUT", "20"))
    except Exception:
        timeout = 20.0
    api_url = os.getenv("JYB_SEARCH_API_URL", DEFAULT_SEARCH_API_URL)
    start_url = os.getenv("JYB_START_URL", SEARCH_URL)
    # Default to blank keywords when env is unset
    raw_keywords = os.getenv("JYB_KEYWORDS", "")
    keywords = [kw.strip() for kw in raw_keywords.split(',') if kw.strip()] or [""]
    try:
        consecutive_stop = int(os.getenv("JYB_EXISTING_CONSECUTIVE_STOP", "5"))
    except Exception:
        consecutive_stop = 5
    if consecutive_stop < 0:
        consecutive_stop = 0

    collected: List[FeedItemLike] = []
    consecutive_hits = 0
    max_pages = max(1, int(pages or 1))

    # Iterate keywords, then pages for each keyword until limit reached
    for kw in keywords:
        page = 1
        while page <= max_pages:
            # Prefer JSON API
            data = None
            for attempt in range(3):
                try:
                    params = {
                        "pagesize": min(50, max(10, (limit or 10))),
                        "pageindex": page,
                        "searchWordStr": kw,
                        "searchStr": kw,
                        "sortstr": "-pubtimestamp",
                    }
                    resp = _request(sess, api_url, params=params, timeout=timeout)
                    data = resp.json()
                    break
                except Exception:
                    time.sleep(0.5 * (2 ** attempt))
            items: List[FeedItemLike] = []
            if isinstance(data, dict):
                arrs: List[dict] = []
                if isinstance(data.get("szbList"), list):
                    arrs += data["szbList"]
                if isinstance(data.get("dataList"), list):
                    arrs += data["dataList"]
                for item in arrs:
                    url = item.get("docpuburl") or item.get("docurl") or item.get("url")
                    if not url:
                        continue
                    if not urlparse(url).scheme:
                        url = urljoin(BASE_URL, url)
                    title = (item.get("title") or "").strip()
                    if not title:
                        continue
                    publish = item.get("pubtime") or item.get("docpubtime") or item.get("doctime")
                    publish_iso = _extract_iso_from_text(str(publish) if publish else "")
                    items.append(FeedItemLike(title=title, url=url, section=None, publish_time_iso=publish_iso, raw=item))
            # Fallback to HTML when API gave nothing
            if not items:
                try:
                    resp = _request(sess, start_url, params={"topsearch": kw, "page": page}, timeout=timeout)
                    html = _response_text(resp)
                    items = _parse_listing_html(html, start_url)
                except Exception:
                    items = []

            if not items:
                break  # no items on this page; stop paging this keyword

            for it in items:
                aid = make_article_id(it.url)
                if existing_ids is not None and aid in existing_ids:
                    if consecutive_stop == 0:
                        continue
                    consecutive_hits += 1
                    if consecutive_hits >= consecutive_stop:
                        return collected
                    continue
                else:
                    consecutive_hits = 0
                collected.append(it)
                if limit is not None and len(collected) >= limit:
                    return collected
            page += 1
    return collected


def _find_content_container(soup: BeautifulSoup) -> Optional[Any]:
    candidates = [
        "#js_content",
        ".xl_text",
        ".new_content",
        "#content",
        "#text",
        "#article",
        "#article-content",
        "#zoom",
        ".text",
        ".content",
        ".article",
        ".article-content",
        ".article-body",
        ".TRS_Editor",
    ]
    for sel in candidates:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return node
    # fallback to largest div by text length
    best = None
    best_len = 0
    for div in soup.find_all("div"):
        text = div.get_text(strip=True)
        if len(text) > best_len:
            best = div
            best_len = len(text)
    return best


def html_to_markdown(html_str: str) -> str:
    # Lightweight conversion similar to other adapters
    text = re.sub(r"<(?:/)?p[^>]*>", "\n\n", html_str or "", flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _extract_detail_title(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.find("h1")
    if not h1:
        return None
    h1_text = h1.get_text(strip=True)
    if not h1_text:
        return None
    parent = h1.parent
    if not parent:
        return h1_text
    h3 = parent.find("h3")
    h4 = parent.find("h4")
    pre_title = h3.get_text(strip=True) if h3 else ""
    post_title = h4.get_text(strip=True) if h4 else ""
    if pre_title or post_title:
        return f"{pre_title}{h1_text}{post_title}"
    return h1_text


def fetch_detail(url: str) -> Dict[str, Any]:
    sess = _session()
    timeout = float(os.getenv("JYB_TIMEOUT", "20"))
    html_text = ""
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            resp = _request(sess, normalize_url(url), timeout=timeout)
            html_text = _response_text(resp)
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(0.5 * (2 ** attempt))
    if not html_text:
        if last_exc:
            raise last_exc
        raise RuntimeError("empty detail response")

    soup = BeautifulSoup(html_text, "html.parser")
    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    detail_title = _extract_detail_title(soup)
    if detail_title:
        title = detail_title

    publish_iso = _extract_iso_from_text(soup.get_text(" ", strip=True))
    content_node = _find_content_container(soup)
    if content_node is None:
        content_html = html_text
    else:
        # strip common noisy nodes
        for bad in content_node.select("script, style, .ad, .adEditor, .copyright, .qr, .qrcode"):
            try:
                bad.decompose()
            except Exception:
                pass
        content_html = content_node.decode_contents()
    text_md = html_to_markdown(content_html)

    return {
        "title": title.strip() if title else None,
        "source": "中国教育报",
        "publish_time": None,
        "publish_time_iso": publish_iso,
        "url": normalize_url(url),
        "content": content_html,
        "content_markdown": text_md,
    }


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
        'source': (data.get('source') or item.section or '中国教育报').strip(),
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

