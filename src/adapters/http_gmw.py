"""Guangming Daily crawler and pipeline adapter utilities."""
from __future__ import annotations

import argparse
import gzip
import http.cookiejar
import io
import json
import logging
import re
import sys
import urllib.request
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

LOGGER = logging.getLogger(__name__)
DEFAULT_LISTING_URL = "https://news.gmw.cn/node_4108.htm"
ARTICLE_URL_PATTERN = re.compile(r"content_\d+\.htm$", re.IGNORECASE)
LISTING_URL_PATTERN = re.compile(r"node_\d+(?:_\d+)?\.htm$", re.IGNORECASE)


@dataclass
class Article:
    """Container for a Guangming Daily article."""

    title: str
    url: str
    publish_time: Optional[str]
    content_markdown: str


class ListingLinkExtractor(HTMLParser):
    """Collects anchor hrefs from a listing page."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        absolute = urljoin(self.base_url, href.strip())
        self.links.append(absolute)


class _SingleSelectorExtractor(HTMLParser):
    """Extracts the first element matching a selector from an HTML document."""

    def __init__(self, selector: Dict[str, str]) -> None:
        super().__init__(convert_charrefs=False)
        self.selector = selector
        self.depth = 0
        self.capture_depth: Optional[int] = None
        self.buffer: List[str] = []
        self.captured: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, Optional[str]]]) -> None:
        if self.captured is not None:
            self.depth += 1
            return
        tag_lower = tag.lower()
        attrs_dict = {name.lower(): (value or "") for name, value in attrs}
        if self.capture_depth is None and self._matches(tag_lower, attrs_dict):
            self.capture_depth = self.depth
        if self.capture_depth is not None:
            start_text = self.get_starttag_text()
            if not start_text:
                start_text = self._reconstruct_starttag(tag_lower, attrs)
            self.buffer.append(start_text)
        self.depth += 1

    def handle_startendtag(self, tag: str, attrs: Sequence[tuple[str, Optional[str]]]) -> None:
        if self.captured is not None:
            return
        tag_lower = tag.lower()
        if self.capture_depth is None and self._matches(tag_lower, {name.lower(): (value or "") for name, value in attrs}):
            text = self._reconstruct_starttag(tag_lower, attrs, self_closing=True)
            self.captured = text

    def handle_data(self, data: str) -> None:
        if self.capture_depth is not None and self.captured is None:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        self.depth -= 1
        if self.capture_depth is None or self.captured is not None:
            return
        self.buffer.append(f"</{tag}>")
        if self.depth == self.capture_depth:
            self.captured = "".join(self.buffer)
            self.capture_depth = None

    def _matches(self, tag: str, attrs: Dict[str, str]) -> bool:
        tag_name = self.selector.get("tag")
        if tag_name and tag != tag_name:
            return False
        selector_id = self.selector.get("id")
        if selector_id and attrs.get("id") != selector_id:
            return False
        selector_class = self.selector.get("class")
        if selector_class:
            classes = attrs.get("class", "").split()
            if selector_class not in classes:
                return False
        return True

    def _reconstruct_starttag(
        self, tag: str, attrs: Sequence[tuple[str, Optional[str]]], self_closing: bool = False
    ) -> str:
        parts = [tag]
        for name, value in attrs:
            if value is None:
                parts.append(name)
            else:
                escaped = value.replace('"', "&quot;")
                parts.append(f'{name}="{escaped}"')
        closing = " /" if self_closing else ""
        return "<" + " ".join(parts) + f"{closing}>"


def _extract_fragment(html: str, selectors: Sequence[Dict[str, str]]) -> str:
    for selector in selectors:
        parser = _SingleSelectorExtractor(selector)
        parser.feed(html)
        if parser.captured:
            return parser.captured
    body_parser = _SingleSelectorExtractor({"tag": "body"})
    body_parser.feed(html)
    if body_parser.captured:
        return body_parser.captured
    return html


class MarkdownRenderer(HTMLParser):
    """Converts a subset of HTML into simple Markdown."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.lines: List[str] = []
        self.current: str = ""
        self.pending_prefix: str = ""
        self.list_stack: List[Dict[str, int]] = []
        self.blockquote_depth = 0
        self.skip_stack: List[str] = []

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        if self.skip_stack:
            if tag_lower in {"script", "style", "noscript"}:
                self.skip_stack.append(tag_lower)
            return
        if tag_lower in {"script", "style", "noscript"}:
            self.skip_stack.append(tag_lower)
            return
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag_lower == "p":
            self._start_block()
        elif tag_lower in {"div", "section", "article"}:
            self._maybe_separate_block()
        elif tag_lower.startswith("h") and len(tag_lower) == 2 and tag_lower[1].isdigit():
            self._start_block()
            level = int(tag_lower[1])
            self.pending_prefix = "#" * level + " "
        elif tag_lower == "br":
            self._flush_current()
        elif tag_lower == "ul":
            self.list_stack.append({"type": "ul", "index": 0})
        elif tag_lower == "ol":
            self.list_stack.append({"type": "ol", "index": 0})
        elif tag_lower == "li":
            self._flush_current()
            indent = "    " * max(len(self.list_stack) - 1, 0)
            prefix = "- "
            if self.list_stack:
                top = self.list_stack[-1]
                if top["type"] == "ol":
                    top["index"] += 1
                    prefix = f"{top['index']}. "
            self.pending_prefix = indent + prefix
        elif tag_lower == "blockquote":
            self._flush_current()
            self.blockquote_depth += 1
        elif tag_lower == "img":
            src = attrs_dict.get("src")
            if src:
                alt = attrs_dict.get("alt", "").strip()
                absolute = urljoin(self.base_url, src)
                self._flush_current()
                self.lines.append(f"![{alt}]({absolute})")
        elif tag_lower == "a":
            href = attrs_dict.get("href")
            if href:
                absolute = urljoin(self.base_url, href)
                self.lines.append(f"<{absolute}>")

    def handle_startendtag(self, tag: str, attrs: Sequence[tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        text = re.sub(r"\s+", " ", data)
        if not text.strip():
            return
        self._ensure_prefix()
        if self.current and not self.current.endswith(" "):
            self.current += " "
        self.current += text.strip()

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        if self.skip_stack:
            if self.skip_stack[-1] == tag_lower:
                self.skip_stack.pop()
            return
        if tag_lower == "p":
            self._flush_current()
            self._append_blank_line()
        elif tag_lower.startswith("h") and len(tag_lower) == 2 and tag_lower[1].isdigit():
            self._flush_current()
            self._append_blank_line()
        elif tag_lower in {"ul", "ol"}:
            self._flush_current()
            if self.list_stack:
                self.list_stack.pop()
            self._append_blank_line()
        elif tag_lower == "li":
            self._flush_current()
        elif tag_lower == "blockquote":
            self._flush_current()
            if self.blockquote_depth:
                self.blockquote_depth -= 1
            self._append_blank_line()

    def _maybe_separate_block(self) -> None:
        if self.current.strip():
            self._flush_current()
            self._append_blank_line()

    def _start_block(self) -> None:
        self._flush_current()
        if self.lines and self.lines[-1] != "":
            self.lines.append("")

    def _ensure_prefix(self) -> None:
        if not self.current:
            prefix = "> " * self.blockquote_depth
            if self.pending_prefix:
                prefix += self.pending_prefix
                self.pending_prefix = ""
            self.current = prefix

    def _flush_current(self) -> None:
        if self.pending_prefix:
            self.pending_prefix = ""
        if self.current.strip():
            self.lines.append(self.current.strip())
        self.current = ""

    def _append_blank_line(self) -> None:
        if not self.lines or self.lines[-1] != "":
            self.lines.append("")

    def render(self, html: str) -> str:
        self.feed(html)
        self.close()
        self._flush_current()
        deduped: List[str] = []
        previous_blank = True
        for line in self.lines:
            text = line.rstrip()
            if not text:
                if not previous_blank:
                    deduped.append("")
                previous_blank = True
                continue
            deduped.append(text)
            previous_blank = False
        while deduped and not deduped[-1]:
            deduped.pop()
        return "\n".join(deduped)


class GMWCrawler:
    """Crawler tailored for the Guangming Daily channel."""

    CONTAINER_SELECTORS: Sequence[Dict[str, str]] = (
        {"id": "articleContent"},
        {"id": "contentMain"},
        {"id": "content"},
        {"class": "article-content"},
        {"class": "contentMain"},
        {"class": "u-mainText"},
        {"tag": "article"},
    )

    def __init__(self, *, base_url: str = DEFAULT_LISTING_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/123.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Referer": "https://news.gmw.cn/",
        }

    def crawl(self, *, max_articles: Optional[int] = None) -> List[Article]:
        listing_queue: List[str] = [self.base_url]
        visited_listings: Set[str] = set()
        seen_articles: Set[str] = set()
        articles: List[Article] = []

        while listing_queue:
            if max_articles is not None and len(articles) >= max_articles:
                break
            listing_url = listing_queue.pop(0)
            if listing_url in visited_listings:
                continue
            visited_listings.add(listing_url)

            try:
                listing_html = self._fetch_html(listing_url)
            except Exception as exc:  # pragma: no cover - logged for visibility
                LOGGER.warning("Skipping listing %s due to %s", listing_url, exc)
                continue

            page_articles, next_listings = self._parse_listing(listing_html, listing_url)
            LOGGER.info(
                "Listing %s yielded %d articles and %d additional listings",
                listing_url,
                len(page_articles),
                len(next_listings),
            )

            for candidate in next_listings:
                if candidate not in visited_listings and candidate not in listing_queue:
                    listing_queue.append(candidate)

            for article_url in page_articles:
                if article_url in seen_articles:
                    continue
                seen_articles.add(article_url)
                if max_articles is not None and len(articles) >= max_articles:
                    break
                try:
                    article_html = self._fetch_html(article_url)
                    article = self._parse_article(article_url, article_html)
                except Exception as exc:  # pragma: no cover - logged for visibility
                    LOGGER.warning("Skipping %s due to %s", article_url, exc)
                    continue
                articles.append(article)

        return articles

    def _fetch_html(self, url: str) -> str:
        LOGGER.debug("Fetching %s", url)
        request = urllib.request.Request(url, headers=self.headers)
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read()
            declared_encoding = response.headers.get_content_charset() or ""
            content_encoding = (response.headers.get("Content-Encoding") or "").lower()
        if "gzip" in content_encoding or raw.startswith(b"\x1f\x8b"):
            try:
                raw = gzip.decompress(raw)
            except OSError:
                LOGGER.debug("Failed to gzip-decompress %s", url, exc_info=True)
        elif "deflate" in content_encoding:
            for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
                try:
                    raw = zlib.decompress(raw, wbits)
                    break
                except zlib.error:
                    continue
            else:
                LOGGER.debug("Failed to deflate-decompress %s", url, exc_info=True)
        candidates = []
        if declared_encoding:
            candidates.append(declared_encoding)
        candidates.extend(["utf-8", "gb18030"])
        tried = set()
        for encoding in candidates:
            lowered = encoding.lower()
            if lowered in tried:
                continue
            tried.add(lowered)
            try:
                return raw.decode(encoding, errors="strict")
            except (LookupError, UnicodeDecodeError):
                continue
        return raw.decode("utf-8", errors="replace")

    def _parse_listing(self, html: str, page_url: str) -> Tuple[List[str], List[str]]:
        extractor = ListingLinkExtractor(page_url)
        extractor.feed(html)
        extractor.close()
        article_urls: List[str] = []
        listing_urls: List[str] = []
        for href in extractor.links:
            if ARTICLE_URL_PATTERN.search(href):
                if href not in article_urls:
                    article_urls.append(href)
                continue
            if LISTING_URL_PATTERN.search(href) and href != page_url:
                if href not in listing_urls:
                    listing_urls.append(href)
        return article_urls, listing_urls

    def _parse_article(self, url: str, html: str) -> Article:
        title = self._extract_title(html)
        publish_time = self._extract_publish_time(html)
        content_markdown = self._extract_content_markdown(html, url)
        return Article(title=title, url=url, publish_time=publish_time, content_markdown=content_markdown)

    def _extract_title(self, html: str) -> str:
        meta_match = re.search(
            r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']",
            html,
            re.IGNORECASE,
        )
        if meta_match:
            return meta_match.group(1).strip()
        preferred_h1 = re.search(
            r"<h1[^>]+(?:class|id)=[\"'][^\"']*(?:title|headline)[^\"']*[\"'][^>]*>(.*?)</h1>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if preferred_h1:
            return _strip_tags(preferred_h1.group(1)).strip()
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            return _strip_tags(title_match.group(1)).strip()
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.IGNORECASE | re.DOTALL)
        if h1_match:
            return _strip_tags(h1_match.group(1)).strip()
        return ""

    def _extract_publish_time(self, html: str) -> Optional[str]:
        meta_names = [
            "publishdate",
            "PubDate",
            "PubTime",
            "og:release_date",
            "article:published_time",
            "dc.date",
            "date",
        ]
        for name in meta_names:
            pattern = re.compile(
                rf"<meta[^>]+(?:name|property)=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']+)[\"']",
                re.IGNORECASE,
            )
            match = pattern.search(html)
            if match:
                return match.group(1).strip()

        selectors = ["#pubtime_baidu", "#pubtime", ".pubtime", ".publish-time", "time"]
        datetime_pattern = re.compile(
            r"(\d{4}[-年./]\d{1,2}[-月./]\d{1,2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)"
        )
        for selector in selectors:
            text = _extract_text_by_selector(html, selector)
            if not text:
                continue
            match = datetime_pattern.search(text)
            if match:
                return self._normalize_datetime(match.group(1))

        for match in datetime_pattern.finditer(html):
            normalized = self._normalize_datetime(match.group(1))
            if normalized:
                return normalized
        return None

    def _normalize_datetime(self, value: str) -> str:
        cleaned = value.strip()
        cleaned = cleaned.replace("年", "-").replace("月", "-").replace("日", " ")
        cleaned = re.sub(r"(?<=\d)/(\d)", r"0\1", cleaned)
        cleaned = re.sub(r"(?<=-)\d(?=-)", lambda m: f"0{m.group(0)}", cleaned)
        cleaned = re.sub(r"(?<=-)\d(?= |$)", lambda m: f"0{m.group(0)}", cleaned)
        cleaned = cleaned.replace("T", " ")
        return cleaned.strip()

    def _extract_content_markdown(self, html: str, page_url: str) -> str:
        fragment = _extract_fragment(html, self.CONTAINER_SELECTORS)
        renderer = MarkdownRenderer(page_url)
        return renderer.render(fragment)


def _extract_text_by_selector(html: str, selector: str) -> str:
    if selector.startswith("#"):
        ident = re.escape(selector[1:])
        pattern = re.compile(rf'<[^>]+id=["\']{ident}["\'][^>]*>(.*?)</[^>]+>', re.IGNORECASE | re.DOTALL)
    elif selector.startswith("."):
        class_name = re.escape(selector[1:])
        pattern = re.compile(
            rf'<[^>]+class=["\'][^"\']*\b{class_name}\b[^"\']*["\'][^>]*>(.*?)</[^>]+>',
            re.IGNORECASE | re.DOTALL,
        )
    else:
        name = re.escape(selector)
        pattern = re.compile(rf'<{name}[^>]*>(.*?)</{name}>', re.IGNORECASE | re.DOTALL)
    match = pattern.search(html)
    if not match:
        return ""
    return _strip_tags(match.group(1))


def _strip_tags(fragment: str) -> str:
    cleaner = re.compile(r"<[^>]+>")
    text = cleaner.sub(" ", fragment)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl Guangming Daily node_4108 channel")
    parser.add_argument("--url", default=DEFAULT_LISTING_URL, help="Listing page to crawl")
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Maximum number of articles to crawl (default: all discovered on the listing page)",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="Request timeout in seconds")
    parser.add_argument("--output", type=str, default=None, help="Optional file path to store JSON result")
    parser.add_argument("--indent", type=int, default=2, help="Indentation to use for JSON output")
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG, INFO, ...)")
    return parser.parse_args(argv)


def _ensure_utf8_stdio() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="strict")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            buffer = getattr(stream, "buffer", None)
            if buffer is None:
                continue
            wrapper = io.TextIOWrapper(buffer, encoding="utf-8", errors="strict")
            setattr(sys, name, wrapper)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    _ensure_utf8_stdio()
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(message)s")

    crawler = GMWCrawler(base_url=args.url, timeout=args.timeout)
    articles = crawler.crawl(max_articles=args.max_articles)

    data = [asdict(article) for article in articles]
    serialized = json.dumps(data, ensure_ascii=False, indent=args.indent)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as file:
            file.write(serialized)
        LOGGER.info("Saved %d articles to %s", len(articles), args.output)
    else:
        print(serialized)


# ---------------------------------------------------------------------------
# Pipeline-facing helpers
# ---------------------------------------------------------------------------
CHINA_TZ = timezone(timedelta(hours=8))
SOURCE_NAME = "光明日报"
DEFAULT_TIMEOUT = 15.0
DEFAULT_BASE_URL = DEFAULT_LISTING_URL


@dataclass
class GMWArticle:
    title: str
    url: str
    publish_time: Optional[int]
    publish_time_iso: Optional[datetime]
    content_markdown: str
    raw_publish_text: Optional[str]


def fetch_articles(
    limit: Optional[int] = None,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> List[GMWArticle]:
    """Return Guangming Daily articles using the integrated crawler implementation."""
    crawler = GMWCrawler(base_url=base_url, timeout=timeout)
    raw_articles = crawler.crawl(max_articles=limit)
    parsed: List[GMWArticle] = []
    for raw in raw_articles:
        publish_ts, publish_dt = _parse_publish_time(raw.publish_time)
        parsed.append(
            GMWArticle(
                title=(raw.title or "").strip(),
                url=raw.url,
                publish_time=publish_ts,
                publish_time_iso=publish_dt,
                content_markdown=_normalize_markdown(raw.content_markdown),
                raw_publish_text=raw.publish_time,
            )
        )
    return parsed


def make_article_id(url: str) -> str:
    """Derive a stable article identifier from the Guangming Daily URL."""
    parsed = urlparse((url or "").strip())
    path = parsed.path or "/"
    path = re.sub(r"\.s?htm[l]?$", "", path, flags=re.IGNORECASE)
    path = re.sub(r"/+", "/", path).strip("/")
    if not path:
        path = "index"
    return f"gmw:{path}"


def article_to_feed_row(article: GMWArticle, article_id: str, *, fetched_at: datetime) -> Dict[str, Any]:
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


def article_to_detail_row(
    article: GMWArticle,
    article_id: str,
    *,
    detail_fetched_at: datetime,
) -> Dict[str, Any]:
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


def _normalize_markdown(value: str) -> str:
    text = (value or "").replace("\r\n", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _parse_publish_time(value: Optional[str]) -> Tuple[Optional[int], Optional[datetime]]:
    if not value:
        return None, None
    cleaned = _normalize_publish_text(value)
    if not cleaned:
        return None, None
    dt = _coerce_datetime(cleaned)
    if dt is None:
        return None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHINA_TZ)
    publish_ts = int(dt.timestamp())
    return publish_ts, dt


def _normalize_publish_text(value: str) -> str:
    text = value.strip()
    replacements = {
        "\u5e74": "-",  # 年
        "\u6708": "-",  # 月
        "\u65e5": " ",  # 日
        "\u65f6": ":",  # 时
        "\u70b9": ":",  # 点
        "\u5206": ":",  # 分
        "\u79d2": "",   # 秒
        "/": "-",
        "\uff0e": ".",
        "\u3002": ".",
    }
    for needle, repl in replacements.items():
        text = text.replace(needle, repl)
    text = re.sub(r"[．。]", ".", text)
    text = re.sub(r"[-]{2,}", "-", text)
    text = re.sub(r":{2,}", ":", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:")


def _coerce_datetime(value: str) -> Optional[datetime]:
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H",
        "%Y-%m-%d",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
    )
    for pattern in candidates:
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    match = re.search(
        r"(\d{4})[-.](\d{1,2})[-.](\d{1,2})(?:\s+(\d{1,2})(?::(\d{1,2})(?::(\d{1,2}))?)?)?",
        value,
    )
    if not match:
        return None
    year, month, day, hour, minute, second = match.groups()
    hour = hour or "0"
    minute = minute or "0"
    second = second or "0"
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
        )
    except ValueError:
        return None


__all__ = [
    "Article",
    "GMWCrawler",
    "DEFAULT_LISTING_URL",
    "parse_args",
    "main",
    "GMWArticle",
    "fetch_articles",
    "make_article_id",
    "article_to_feed_row",
    "article_to_detail_row",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT",
    "SOURCE_NAME",
]


if __name__ == "__main__":
    main()
