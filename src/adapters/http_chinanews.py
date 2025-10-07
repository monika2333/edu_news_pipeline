from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
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
    })
    return s


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


def list_items(limit: Optional[int] = None, pages: Optional[int] = None, *, existing_ids: Optional[Set[str]] = None) -> List[FeedItemLike]:
    sess = _session()
    collected: List[FeedItemLike] = []
    total_pages = max(1, int(pages or 1))
    for page in range(1, total_pages + 1):
        if limit is not None and len(collected) >= limit:
            break
        url = "https://www.chinanews.com.cn/scroll-news/news1.html" if page == 1 else f"https://www.chinanews.com.cn/scroll-news/news1-{page}.html"
        resp = sess.get(url, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        ul = soup.select_one(".content_list")
        if not ul:
            break
        for li in ul.select("li"):
            a = li.select_one(".dd_bt a") or li.select_one("a")
            if not a or not a.get("href"):
                continue
            href = normalize_url(a["href"]) 
            title = (a.get_text() or "").strip()
            section = None
            lm = li.select_one(".dd_lm")
            if lm:
                section = (lm.get_text() or "").strip()
            tnode = li.select_one(".dd_time")
            iso: Optional[str] = None
            if tnode:
                now_local = datetime.now(tz=timezone.utc).astimezone()
                hhmm = (tnode.get_text() or "").strip()
                m = re.match(r"(\d{1,2}):(\d{2})", hhmm)
                if m:
                    try:
                        iso = now_local.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0).isoformat()
                    except Exception:
                        iso = None
            item = FeedItemLike(title=title, url=href, section=section, publish_time_iso=iso, raw={})
            aid = make_article_id(href)
            if existing_ids is not None and aid in existing_ids:
                continue
            collected.append(item)
            if limit is not None and len(collected) >= limit:
                break
    return collected


def fetch_detail(url: str) -> Dict[str, Any]:
    sess = _session()
    resp = sess.get(normalize_url(url), timeout=15)
    resp.raise_for_status()
    html = resp.text
    soup = BeautifulSoup(html, "html.parser")
    title = None
    h1 = soup.select_one("h1")
    if h1:
        title = (h1.get_text() or "").strip()
    source = None
    content_node = soup.select_one("#p-detail") or soup.select_one(".left_zw") or soup.select_one("#content") or soup.select_one("article")
    content_html = content_node.decode_contents() if content_node else html
    text_md = html_to_markdown(content_html)
    return {
        "title": title,
        "source": source,
        "publish_time": None,
        "publish_time_iso": None,
        "url": normalize_url(url),
        "content": content_html,
        "content_markdown": text_md,
    }


def feed_item_to_row(item: FeedItemLike, article_id: str, *, fetched_at: datetime) -> Dict[str, Any]:
    return {
        'token': None,
        'profile_url': None,
        'article_id': article_id,
        'title': item.title,
        'source': item.section,
        'publish_time': None,
        'publish_time_iso': item.publish_time_iso,
        'url': item.url,
        'summary': None,
        'comment_count': None,
        'digg_count': None,
        'fetched_at': fetched_at,
    }


def build_detail_update(item: FeedItemLike, article_id: str, data: Dict[str, Any], *, detail_fetched_at: datetime) -> Dict[str, Any]:
    return {
        'token': None,
        'profile_url': None,
        'article_id': article_id,
        'title': (data.get('title') or item.title or '').strip(),
        'source': (data.get('source') or item.section or '').strip(),
        'publish_time': data.get('publish_time'),
        'publish_time_iso': data.get('publish_time_iso') or item.publish_time_iso,
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


