from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

# China timezone for ChinaNews timestamps
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


# ---- Source extraction aligned with chinanews_crawler.py ----
SOURCE_SELECTORS: Sequence[str] = (
    "#source_baidu",
    ".content_left_time",
    ".content_left .content_left_time",
    ".left_time",
    ".content_title .left",
    ".content_title p",
    ".left .time-source",
    ".newsInfo",
    ".cont .source",
)


def _normalize_ws(text: str) -> str:
    s = (text or "").replace("\xa0", " ").replace("\u3000", " ").strip()
    return re.sub(r"[ \t\r\f\v]+", " ", s)


def _clean_source_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    s = _normalize_ws(text)
    m = re.search(r"来源[:：]\s*(.+)", s)
    if m:
        s = m.group(1)
    # remove tail markers like 编辑/责任编辑/作者/记者
    s = re.sub(r"(责任编辑|编辑|作者|记者)[:：]?.*$", "", s).strip()
    s = s.strip("【】[]()（）")
    return s or None


def _extract_source_from_soup(soup: BeautifulSoup) -> Optional[str]:
    for sel in SOURCE_SELECTORS:
        node = soup.select_one(sel)
        if not node:
            continue
        link = node.find("a")
        if link and link.get_text(strip=True):
            cleaned = _clean_source_text(link.get_text(strip=True))
            if cleaned:
                return cleaned
        text = node.get_text(" ", strip=True)
        cleaned = _clean_source_text(text)
        if cleaned:
            return cleaned
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


def _dt_from_iso(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None


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
        soup = BeautifulSoup(resp.content, "html.parser")
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
            # Build publish iso from URL date + time (e.g., 10-7 23:43)
            base_date = _date_from_url(href) or datetime.now(tz=CHINA_TZ)
            hh = mm = 0
            if tnode:
                text = (tnode.get_text() or "").strip()
                m = re.match(r"(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})", text)
                if m:
                    try:
                        # month, day = int(m.group(1)), int(m.group(2))  # could cross-check with URL
                        hh, mm = int(m.group(3)), int(m.group(4))
                    except Exception:
                        hh = mm = 0
                else:
                    m2 = re.search(r"(\d{1,2}):(\d{2})", text)
                    if m2:
                        hh, mm = int(m2.group(1)), int(m2.group(2))
            publish_dt = base_date.replace(hour=hh, minute=mm, second=0, microsecond=0)
            iso: Optional[str] = publish_dt.isoformat()
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
    html_bytes = resp.content
    soup = BeautifulSoup(html_bytes, "html.parser")

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

    # Source extraction aligned with crawler: prefer visible nodes, then meta
    source = _extract_source_from_soup(soup) or _get_meta(soup, names=["source"]) or _get_meta(soup, props=["og:site_name"]) or "ChinaNews"

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

    if not publish_iso or not source:
        time_line = None
        cand = _first(soup, [".left-time", ".time", ".news-time", ".content .time"]) or None
        if cand:
            time_line = cand.get_text(" ", strip=True)
        if time_line:
            if not publish_iso:
                dt = _parse_datetime_str(time_line)
                if dt:
                    publish_iso = dt.isoformat()
            if not source:
                source = _clean_source_text(time_line) or source

    # Content with multiple fallbacks
    content_node = None
    for selector in ("#p-detail", ".left_zw", "#content", "article", ".content"):
        content_node = soup.select_one(selector)
        if content_node and len(content_node.get_text(strip=True)) > 40:
            break
        content_node = None
    content_html = content_node.decode_contents() if content_node else (html_bytes.decode(errors='ignore'))
    text_md = html_to_markdown(content_html)

    # Per current requirement, prefer feed time; do not override from detail.
    publish_time_iso = None
    publish_time = None

    if title:
        title = title.strip()
    if source:
        source = source.strip()
    return {
        "title": title,
        "source": source,
        "publish_time": publish_time,
        "publish_time_iso": publish_time_iso,
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
        'source': (data.get('source') or item.section or '').strip(),
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
