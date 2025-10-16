"""China Daily crawler that exports selected articles to a JSON file.

The script walks the listing pages for the provided China Daily channel,
downloads each article, converts the main content to Markdown-flavoured text,
and saves the collected data as a JSON array.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, NavigableString, Tag

DEFAULT_START_URL = (
    "https://cn.chinadaily.com.cn/5b753f9fa310030f813cf408/"
    "5bd54dd6a3101a87ca8ff5f8/5bd54e59a3101a87ca8ff606"
)
DEFAULT_OUTPUT = "chinadaily_articles.json"
DEFAULT_DELAY = 0.5
DEFAULT_TIMEOUT = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
)
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?")


@dataclass
class ListingEntry:
    title: str
    url: str
    publish_time: Optional[str]


def absolute_url(source_url: str, link: Optional[str]) -> Optional[str]:
    if not link:
        return None
    link = link.strip()
    if not link or link.lower().startswith("javascript"):
        return None
    return urljoin(source_url, link)


def clean_text(text: str) -> str:
    return " ".join(text.split())


def fetch_html(url: str, session: requests.Session, timeout: int) -> str:
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    # Rely on requests' encoding detection; override only when missing.
    if response.encoding is None:
        response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def extract_publish_time_from_text(text: str) -> Optional[str]:
    matches = DATE_PATTERN.findall(text)
    return matches[-1] if matches else None


def parse_listing_page(html: str, page_url: str) -> Tuple[List[ListingEntry], Optional[str]]:
    soup = BeautifulSoup(html, "html.parser")
    entries: List[ListingEntry] = []

    for h3 in soup.select("div.left-liebiao h3"):
        link = h3.find("a", href=True)
        if not link:
            continue
        article_url = absolute_url(page_url, link["href"])
        if not article_url:
            continue

        title = clean_text(link.get_text())
        publish_time: Optional[str] = None

        container = h3.find_parent(class_=re.compile(r"busBox", re.I)) or h3.parent
        p_tag = container.find("p") if container else None
        if p_tag:
            bold = p_tag.find("b")
            if bold and bold.get_text(strip=True):
                publish_time = bold.get_text(strip=True)
            else:
                publish_time = extract_publish_time_from_text(p_tag.get_text(" ", strip=True))

        entries.append(ListingEntry(title=title, url=article_url, publish_time=publish_time))

    next_page_url: Optional[str] = None
    for anchor in soup.select("a.pagestyle[href]"):
        text = anchor.get_text(strip=True)
        if text and "下一页" in text:
            next_page_url = absolute_url(page_url, anchor["href"])
            break

    return entries, next_page_url


def extract_title(soup: BeautifulSoup) -> Optional[str]:
    title_tag = soup.find("h1")
    if title_tag and title_tag.get_text(strip=True):
        return clean_text(title_tag.get_text())

    meta_candidates = [
        ("property", "og:title"),
        ("name", "twitter:title"),
        ("name", "headline"),
    ]
    for attr, value in meta_candidates:
        meta = soup.find("meta", attrs={attr: value})
        if meta and meta.get("content"):
            return clean_text(meta["content"])
    if soup.title and soup.title.get_text(strip=True):
        return clean_text(soup.title.get_text())
    return None


def extract_publish_time_from_article(soup: BeautifulSoup) -> Optional[str]:
    meta_names = [
        "publishdate",
        "PubDate",
        "pubdate",
        "publish_time",
        "article:published_time",
        "og:pubdate",
    ]
    for name in meta_names:
        meta = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if meta and meta.get("content"):
            return clean_text(meta["content"])

    date_classes = [
        "date",
        "datetxt",
        "show_Date",
        "time",
        "data",  # common typo on site
    ]
    for class_name in date_classes:
        elem = soup.find(class_=class_name)
        if elem and elem.get_text(strip=True):
            candidate = extract_publish_time_from_text(elem.get_text(" ", strip=True))
            if candidate:
                return candidate
    return None


def find_content_container(soup: BeautifulSoup) -> Optional[Tag]:
    selectors = [
        "#Content",
        ".content",
        ".contentMain",
        ".main-content",
        ".main_artic",
        ".main-artic",
        ".article",
        ".article-left-new",
        ".articleContent",
        ".article-content",
        ".left_zw",
        ".TRS_Editor",
        "article",
    ]
    for selector in selectors:
        element = soup.select_one(selector)
        if element and element.get_text(strip=True):
            return element

    for div in soup.find_all("div"):
        identifier = " ".join(filter(None, [div.get("id", ""), " ".join(div.get("class", []))])).lower()
        if "content" in identifier or "article" in identifier:
            if div.get_text(strip=True):
                return div
    return None


def collapse_lines(text: str) -> str:
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    collapsed: List[str] = []
    blank_pending = False
    for line in lines:
        if not line:
            if not blank_pending and collapsed:
                collapsed.append("")
            blank_pending = True
        else:
            collapsed.append(line)
            blank_pending = False
    return "\n".join(collapsed).strip()


def block_markdown(html_fragment: Tag, base_url: str) -> str:
    fragment = BeautifulSoup(str(html_fragment), "html.parser")

    for br in fragment.find_all("br"):
        br.replace_with("\n")

    for anchor in fragment.find_all("a"):
        href = absolute_url(base_url, anchor.get("href"))
        link_text = clean_text(anchor.get_text(" ", strip=True))
        if href:
            replacement = f"[{link_text or href}]({href})"
        else:
            replacement = link_text
        anchor.replace_with(replacement)

    for image in fragment.find_all("img"):
        src = absolute_url(base_url, image.get("data-src") or image.get("src"))
        alt = clean_text(image.get("alt", "")) if image.get("alt") else ""
        if src:
            replacement = f"![{alt}]({src})" if alt else f"![]({src})"
        else:
            replacement = alt
        image.replace_with(replacement)

    text = fragment.get_text("\n")
    return collapse_lines(text)


def list_markdown(list_element: Tag, base_url: str) -> str:
    is_ordered = list_element.name.lower() == "ol"
    lines: List[str] = []
    for index, li in enumerate(list_element.find_all("li", recursive=False), start=1):
        body = block_markdown(li, base_url)
        if not body:
            continue
        bullet = f"{index}." if is_ordered else "-"
        body_lines = body.splitlines()
        lines.append(f"{bullet} {body_lines[0]}")
        for continuation in body_lines[1:]:
            lines.append(f"  {continuation}")
    return "\n".join(lines)


def table_markdown(table_element: Tag, base_url: str) -> str:
    rows: List[List[str]] = []
    for tr in table_element.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        row: List[str] = []
        for cell in cells:
            cell_text = block_markdown(cell, base_url).replace("\n", " ")
            row.append(cell_text)
        rows.append(row)

    if not rows:
        return ""

    max_cols = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (max_cols - len(row)) for row in rows]

    header = normalized_rows[0]
    separator = ["---"] * max_cols
    lines = [" | ".join(header), " | ".join(separator)]
    for row in normalized_rows[1:]:
        lines.append(" | ".join(row))
    return "\n".join(lines)


def content_to_markdown(container: Tag, base_url: str) -> str:
    blocks: List[str] = []

    for child in container.children:
        if isinstance(child, NavigableString):
            text = clean_text(str(child))
            if text:
                blocks.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        name = child.name.lower()
        if name in {"script", "style"}:
            continue
        if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            level = int(name[1])
            heading = block_markdown(child, base_url)
            if heading:
                blocks.append(f"{'#' * level} {heading}")
            continue
        if name in {"ul", "ol"}:
            md = list_markdown(child, base_url)
            if md:
                blocks.append(md)
            continue
        if name == "table":
            md = table_markdown(child, base_url)
            if md:
                blocks.append(md)
            continue
        text = block_markdown(child, base_url)
        if text:
            blocks.append(text)

    filtered_blocks = [block for block in blocks if block.strip()]
    return "\n\n".join(filtered_blocks).strip()


def parse_article(html: str, article_url: str) -> Tuple[Optional[str], Optional[str], str]:
    soup = BeautifulSoup(html, "html.parser")
    title = extract_title(soup)
    publish_time = extract_publish_time_from_article(soup)
    container = find_content_container(soup)
    if container is None:
        raise ValueError("Unable to locate article content block.")
    content_md = content_to_markdown(container, article_url)
    if not content_md:
        raise ValueError("Extracted article content is empty.")
    return title, publish_time, content_md


def crawl_channel(
    start_url: str,
    output_path: str,
    delay: float,
    timeout: int,
    max_pages: Optional[int],
    max_articles: Optional[int],
) -> List[dict]:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    results: List[dict] = []
    seen_urls: set[str] = set()

    page_url: Optional[str] = start_url
    page_index = 0

    while page_url:
        page_index += 1
        print(f"[page {page_index}] Fetching listing: {page_url}")

        try:
            listing_html = fetch_html(page_url, session, timeout)
        except requests.RequestException as exc:
            print(f"Failed to fetch listing {page_url}: {exc}", file=sys.stderr)
            break

        entries, next_page_url = parse_listing_page(listing_html, page_url)

        if not entries:
            print(f"No articles found on listing {page_url}", file=sys.stderr)

        for entry in entries:
            if max_articles is not None and len(results) >= max_articles:
                page_url = None
                break
            if entry.url in seen_urls:
                continue
            seen_urls.add(entry.url)

            print(f"  -> Fetching article: {entry.url}")
            try:
                article_html = fetch_html(entry.url, session, timeout)
                article_title, article_publish_time, content_md = parse_article(article_html, entry.url)
            except requests.RequestException as exc:
                print(f"     Request failed for {entry.url}: {exc}", file=sys.stderr)
                continue
            except ValueError as exc:
                print(f"     Skipping {entry.url}: {exc}", file=sys.stderr)
                continue

            record = {
                "title": article_title or entry.title,
                "url": entry.url,
                "publish_time": entry.publish_time or article_publish_time,
                "content_markdown": content_md,
            }
            results.append(record)

            if delay:
                time.sleep(delay)

        if max_articles is not None and len(results) >= max_articles:
            break
        if max_pages is not None and page_index >= max_pages:
            break

        page_url = next_page_url
        if page_url and delay:
            time.sleep(delay)

    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, ensure_ascii=False, indent=2)
    print(f"Wrote {len(results)} articles to {output_path}")
    return results


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="China Daily channel crawler")
    parser.add_argument(
        "--start-url",
        default=DEFAULT_START_URL,
        help="Listing URL to start crawling from.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help="Path to the JSON file to be written.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help="Delay (in seconds) between HTTP requests.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Optional limit on the number of listing pages to crawl.",
    )
    parser.add_argument(
        "--max-articles",
        type=int,
        default=None,
        help="Optional limit on the total number of articles to capture.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    crawl_channel(
        start_url=args.start_url,
        output_path=args.output,
        delay=max(args.delay, 0.0),
        timeout=max(args.timeout, 1),
        max_pages=args.max_pages,
        max_articles=args.max_articles,
    )


if __name__ == "__main__":
    main()
