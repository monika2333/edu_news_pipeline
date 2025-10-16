"""Crawler for http://www.jyb.cn/search.html?topsearch=

This module provides a small crawler that fetches search results from the
JYB (Ministry of Education of China) website and extracts the article title,
URL, publish time and content rendered as Markdown.

Example usage from the command line::

    python jyb_spider.py 教育 政策 --limit 3

The crawler tries to be defensive against minor structural changes of the
website by using multiple fallbacks when it looks for the search result list
or the main content container of an article.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import random
import re
import sys
import time
from typing import Iterable, Iterator, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag


LOGGER = logging.getLogger(__name__)

BASE_URL = "http://www.jyb.cn"
SEARCH_URL = f"{BASE_URL}/search.html"
SEARCH_API_URL = "http://new.jyb.cn/jybuc/hyBaseCol/search.action"


@dataclasses.dataclass
class Article:
    """Structured information extracted for a single article."""

    title: str
    url: str
    publish_time: Optional[str]
    content_markdown: str


class JYBSpider:
    """Crawler implementation for search results on jyb.cn."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.session = requests.Session()
        # Disable environment proxy settings to avoid accidental routing through
        # unused local proxies (common on development machines).
        self.session.trust_env = False
        self.timeout = timeout
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Referer": BASE_URL,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9, */*;q=0.8",
                "Connection": "keep-alive",
            }
        )

    def search(self, keyword: str, *, page: int = 1, limit: Optional[int] = None) -> List[Article]:
        """Search for `keyword` and return a list of :class:`Article` objects.

        Args:
            keyword: The keyword to search for.
            page: Result page to fetch (1-based).
            limit: Maximum number of articles to return. ``None`` means all.
        """

        LOGGER.info("Fetching search results for %s (starting page %s)", keyword, page)

        articles: List[Article] = []
        seen_urls: set[str] = set()
        current_page = page

        page_size = 10
        if limit is not None:
            page_size = min(50, max(10, limit))

        no_progress_pages = 0

        while True:
            api_results = self._fetch_search_results_api(keyword, page=current_page, page_size=page_size)

            if not api_results:
                if not articles and not seen_urls:
                    response = self._request(
                        SEARCH_URL,
                        params={"topsearch": keyword, "page": page},
                    )
                    soup = BeautifulSoup(response.text, "html.parser")
                    for result in self._parse_search_results(soup):
                        if result.url in seen_urls:
                            continue
                        seen_urls.add(result.url)
                        try:
                            article = self._fetch_article(result)
                        except Exception as exc:  # pylint: disable=broad-except
                            LOGGER.warning("Failed to fetch article %s: %s", result.url, exc)
                            continue
                        articles.append(article)
                        if limit is not None and len(articles) >= limit:
                            return articles
                break

            added_any = False
            for result in api_results:
                if result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                try:
                    article = self._fetch_article(result)
                except Exception as exc:  # pylint: disable=broad-except
                    LOGGER.warning("Failed to fetch article %s: %s", result.url, exc)
                    continue
                articles.append(article)
                added_any = True
                if limit is not None and len(articles) >= limit:
                    return articles

            if limit is None:
                return articles
            if not added_any:
                no_progress_pages += 1
                if no_progress_pages >= 3:
                    break
            else:
                no_progress_pages = 0
            current_page += 1

        return articles

    def _request(self, url: str, *, params: Optional[dict] = None) -> requests.Response:
        """Wrapper around :meth:`requests.Session.get` with 403 mitigation."""

        headers = {}
        # Some deployments block repeated requests from the same IP.
        # A random X-Forwarded-For header can help to avoid hard blocking
        # when the site runs behind certain CDNs.
        headers["X-Forwarded-For"] = ".".join(str(random.randint(1, 255)) for _ in range(4))

        response = self.session.get(url, params=params, timeout=self.timeout, headers=headers)
        response.raise_for_status()
        # Ensure correct decoding for sites that rely on legacy encodings such as GBK.
        apparent_encoding = response.apparent_encoding
        if apparent_encoding:
            response.encoding = apparent_encoding
        return response

    def _parse_search_results(self, soup: BeautifulSoup) -> Iterator["_SearchResult"]:
        """Yield search results from the parsed HTML soup."""

        # The search page contains one or more containers that host the result list.
        containers: Iterable[Tag] = (
            soup.select(".res-list li")
            or soup.select(".search-result li")
            or soup.select(".list-left li")
            or soup.select(".clist li")
            or soup.find_all("li")
        )

        seen: set[str] = set()
        for item in containers:
            link = item.find("a", href=True)
            if not link:
                continue

            href_raw = link["href"]
            if href_raw and href_raw.strip().lower().startswith("javascript:"):
                continue
            href = urljoin(BASE_URL, href_raw) if not urlparse(href_raw).scheme else href_raw
            title = link.get_text(strip=True)
            if not title:
                continue

            if href in seen:
                continue
            seen.add(href)

            publish_time = self._extract_date(item.get_text(" ", strip=True))
            yield _SearchResult(title=title, url=href, publish_time=publish_time)

    def _fetch_search_results_api(self, keyword: str, *, page: int, page_size: int) -> List["_SearchResult"]:
        """Fetch search results via the JSON API used by the website."""

        params = {
            "pagesize": page_size,
            "pageindex": page,
            "searchWordStr": keyword,
            "searchStr": keyword,
            "sortstr": "-pubtimestamp",
        }

        data = None
        for attempt in range(3):
            try:
                response = self._request(SEARCH_API_URL, params=params)
                data = response.json()
                break
            except Exception as exc:  # pylint: disable=broad-except
                LOGGER.warning(
                    "Search API request failed for %s (page %s, attempt %s/3): %s",
                    keyword,
                    page,
                    attempt + 1,
                    exc,
                )
                if attempt == 2:
                    return []
                time.sleep(1.0)
        if data is None:
            return []

        items: List[dict] = []
        if isinstance(data, dict):
            if isinstance(data.get("szbList"), list):
                items.extend(data["szbList"])
            if isinstance(data.get("dataList"), list):
                items.extend(data["dataList"])

        results: List[_SearchResult] = []
        for item in items:
            url = item.get("docpuburl") or item.get("docurl") or item.get("url")
            if not url:
                continue
            url = urljoin(BASE_URL, url) if not urlparse(url).scheme else url
            title = (item.get("title") or "").strip()
            if not title:
                continue
            publish_time = item.get("pubtime") or item.get("docpubtime") or item.get("doctime")
            results.append(_SearchResult(title=title, url=url, publish_time=publish_time))
        return results

    def _fetch_article(self, result: "_SearchResult") -> Article:
        response = self._request(result.url)
        soup = BeautifulSoup(response.text, "html.parser")

        title = self._extract_title(soup) or result.title
        publish_time = result.publish_time or self._extract_date(soup.get_text(" ", strip=True))
        content_container = self._find_content_container(soup)
        if not content_container:
            raise RuntimeError("Could not locate article content container")

        content_markdown = self._html_to_markdown(content_container, base_url=result.url)
        return Article(
            title=title,
            url=result.url,
            publish_time=publish_time,
            content_markdown=content_markdown,
        )

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> Optional[str]:
        title = None
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            title = h1.get_text(strip=True)
        return title

    @staticmethod
    def _extract_date(text: str) -> Optional[str]:
        match = re.search(r"(20\d{2}-\d{2}-\d{2}(?:[\sT]\d{2}:\d{2}(?::\d{2})?)?)", text)
        if match:
            return match.group(1).replace("T", " ")
        return None

    @staticmethod
    def _find_content_container(soup: BeautifulSoup) -> Optional[Tag]:
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
        for selector in candidates:
            node = soup.select_one(selector)
            if node and node.get_text(strip=True):
                refined = JYBSpider._refine_content_node(node)
                if refined and refined.get_text(strip=True):
                    return refined
        # Fall back to the largest <div> measured by text length.
        best = None
        best_len = 0
        for div in soup.find_all("div"):
            text = div.get_text(strip=True)
            if len(text) > best_len:
                best = div
                best_len = len(text)
        return best

    @staticmethod
    def _refine_content_node(node: Tag) -> Optional[Tag]:
        """Try to pick a more focused descendant containing the article body."""

        refinements = [
            ".xl_text",
            ".new_content",
            ".article-text",
            ".articleText",
            ".news-con",
            ".TRS_Editor",
        ]
        for selector in refinements:
            inner = node.select_one(selector)
            if inner and inner.get_text(strip=True):
                return inner
        return node

    def _html_to_markdown(self, node: Tag, *, base_url: str) -> str:
        parts: List[str] = []
        for block in self._iter_blocks(node):
            parts.append(self._render_block(block, base_url=base_url))
        return "\n\n".join(part for part in parts if part)

    def _iter_blocks(self, node: Tag) -> Iterator[Tag | NavigableString]:
        for child in node.children:
            if isinstance(child, Comment):
                continue
            if isinstance(child, Tag) and child.name in {"script", "style"}:
                continue
            if isinstance(child, NavigableString):
                if child.strip():
                    yield child
            elif child.name in {"p", "div", "section", "article"}:
                if child.get_text(strip=True):
                    yield child
            elif child.name in {"h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "pre", "blockquote", "table"}:
                yield child
            else:
                # Recurse into unknown tags to avoid losing nested content.
                yield from self._iter_blocks(child)

    def _render_block(self, block: Tag | NavigableString, *, base_url: str) -> str:
        if isinstance(block, NavigableString):
            return block.strip()
        name = block.name.lower()
        if name.startswith("h") and len(name) == 2 and name[1].isdigit():
            level = int(name[1])
            text = block.get_text(" ", strip=True)
            return f"{'#' * level} {text}"
        if name == "p" or name == "div" or name == "section" or name == "article":
            return self._render_inline(block, base_url=base_url)
        if name == "ul":
            return "\n".join(
                f"- {self._render_inline(li, base_url=base_url)}" for li in block.find_all("li", recursive=False)
            )
        if name == "ol":
            lines = []
            for idx, li in enumerate(block.find_all("li", recursive=False), start=1):
                lines.append(f"{idx}. {self._render_inline(li, base_url=base_url)}")
            return "\n".join(lines)
        if name == "pre":
            return f"``````\n{block.get_text()}\n``````"
        if name == "blockquote":
            text = self._render_inline(block, base_url=base_url)
            return "\n".join(f"> {line}" for line in text.splitlines())
        if name == "table":
            return self._render_table(block, base_url=base_url)
        return self._render_inline(block, base_url=base_url)

    def _render_inline(self, node: Tag, *, base_url: str) -> str:
        pieces: List[str] = []
        for child in node.descendants:
            if isinstance(child, Comment):
                continue
            if isinstance(child, Tag) and child.name in {"script", "style"}:
                continue
            if isinstance(child, NavigableString):
                pieces.append(child.strip())
            elif child.name == "br":
                pieces.append("\n")
            elif child.name == "strong" or child.name == "b":
                text = child.get_text(" ", strip=True)
                if text:
                    pieces.append(f"**{text}**")
            elif child.name == "em" or child.name == "i":
                text = child.get_text(" ", strip=True)
                if text:
                    pieces.append(f"*{text}*")
            elif child.name == "a" and child.get("href"):
                href = urljoin(base_url, child["href"])
                text = child.get_text(" ", strip=True)
                pieces.append(f"[{text}]({href})")
            elif child.name == "img":
                src = urljoin(base_url, child.get("src", ""))
                alt = child.get("alt", "")
                pieces.append(f"![{alt}]({src})")
        # Normalize whitespace while preserving intentional new lines.
        text = " ".join(filter(None, (piece.strip() for piece in pieces if piece != "\n")))
        # Add explicit newlines for <br> occurrences.
        if "\n" in pieces:
            newline_text = []
            for piece in pieces:
                if piece == "\n":
                    newline_text.append("\n")
                elif piece:
                    newline_text.append(piece)
            text = "".join(newline_text)
        return re.sub(r"\n{3,}", "\n\n", text.strip())

    def _render_table(self, table: Tag, *, base_url: str) -> str:
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for cell in tr.find_all(["th", "td"]):
                cells.append(self._render_inline(cell, base_url=base_url))
            rows.append(cells)
        if not rows:
            return ""
        widths = [max(len(row[i]) for row in rows if i < len(row)) for i in range(max(len(r) for r in rows))]

        def format_row(row: List[str]) -> str:
            padded = [cell.ljust(widths[idx]) for idx, cell in enumerate(row)]
            return " | ".join(padded)

        header = format_row(rows[0])
        separator = " | ".join("-" * width for width in widths)
        body = "\n".join(format_row(row) for row in rows[1:])
        return "\n".join([header, separator, body]) if body else "\n".join([header, separator])


@dataclasses.dataclass
class _SearchResult:
    title: str
    url: str
    publish_time: Optional[str]


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search crawler for jyb.cn")
    parser.add_argument(
        "keywords",
        nargs="*",
        help="Search keywords (optional; leave empty to fetch default results)",
    )
    parser.add_argument("--page", type=int, default=1, help="Result page number (1-based)")
    parser.add_argument("--limit", type=int, default=3, help="Maximum number of articles to fetch")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))

    keyword = " ".join(args.keywords)
    spider = JYBSpider()

    for article in spider.search(keyword, page=args.page, limit=args.limit):
        print("Title:", article.title)
        print("URL:", article.url)
        if article.publish_time:
            print("Publish time:", article.publish_time)
        print("Content (markdown):\n")
        print(article.content_markdown)
        print("\n" + "=" * 80 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
