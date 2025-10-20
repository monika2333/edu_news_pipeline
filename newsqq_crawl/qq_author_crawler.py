#!/usr/bin/env python3
"""
Crawler for Tencent News author pages.

Given an author page URL (or author suid), the script fetches the article list,
retrieves each article's content, converts it to markdown, and writes the result
to a JSON file. Multiple authors can be listed in qq_author.txt (one per line)
and processed in a single run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from bs4.element import Comment

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

AUTHOR_INFO_API = "https://i.news.qq.com/i/getUserHomepageInfo"
ARTICLE_LIST_API = "https://i.news.qq.com/getSubNewsMixedList"


@dataclass
class ArticleSummary:
    article_id: str
    title: str
    url: str
    publish_time: Optional[str]


@dataclass
class ArticleDetail:
    publish_time: Optional[str]
    markdown: str


def parse_author_id(raw: str) -> str:
    """Extract the author identifier from a URL or return the raw string."""
    if re.match(r"^https?://", raw):
        parsed = urlparse(raw)
        segments = [segment for segment in parsed.path.split("/") if segment]
        if not segments:
            raise ValueError(f"Unable to derive author id from URL: {raw}")
        return segments[-1]
    return raw


def safe_request_json(url: str, params: Dict[str, str]) -> Dict:
    """Perform a GET request expecting JSON and return the parsed payload."""
    response = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    ret = data.get("ret")
    if ret not in (None, 0):
        msg = data.get("errmsg") or data.get("msg") or "unknown error"
        raise RuntimeError(f"API error {ret}: {msg}")
    return data


def fetch_author_profile(author_id: str) -> Dict:
    """Retrieve author metadata, including channel information."""
    params = {
        "guestSuid": author_id,
        "apptype": "web",
        "from_scene": "103",
        "isInGuest": "1",
    }
    data = safe_request_json(AUTHOR_INFO_API, params)
    userinfo = data.get("userinfo") or {}
    if not userinfo:
        raise RuntimeError("Author info not found in response.")
    return userinfo


def fetch_article_summaries(
    author_id: str,
    tab_id: str,
    max_pages: int,
    delay_seconds: float,
) -> List[ArticleSummary]:
    """Pull paginated article list data and return a list of summaries."""
    summaries: List[ArticleSummary] = []
    offset = ""
    for page in range(max_pages):
        params = {
            "guestSuid": author_id,
            "tabId": tab_id,
            "caller": "1",
            "from_scene": "103",
            "visit_type": "guest",
            "offset_info": offset,
        }
        payload = safe_request_json(ARTICLE_LIST_API, params)
        items: Iterable[Dict] = payload.get("newslist") or []
        for item in items:
            article_id = item.get("id") or item.get("article_id")
            title = (item.get("title") or "").strip()
            url = item.get("url") or item.get("surl") or ""
            publish_time = item.get("time") or item.get("pubtime")
            if not (article_id and title and url):
                continue
            summaries.append(
                ArticleSummary(
                    article_id=article_id,
                    title=title,
                    url=url,
                    publish_time=publish_time,
                )
            )
        has_next = bool(payload.get("hasNext"))
        offset = payload.get("offsetInfo") or ""
        if not has_next or not offset:
            break
        if delay_seconds:
            time.sleep(delay_seconds)
    return summaries


def extract_data_block(html: str) -> Dict:
    """Parse the embedded DATA JavaScript block from an article page."""
    match = re.search(r"DATA\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not match:
        raise RuntimeError("DATA Block not found in article page.")
    raw_json = match.group(1)
    return json.loads(raw_json)


def html_to_markdown(html: str) -> str:
    """Convert the HTML snippet to a lightweight markdown string."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(class_="rich_media_content") or soup

    # Drop scripts, styles, and comments that would pollute output.
    for element in container.find_all(["script", "style"]):
        element.decompose()
    for element in container.find_all(string=lambda text: isinstance(text, Comment)):
        element.extract()

    # Normalise line breaks first.
    for br in container.find_all("br"):
        br.replace_with("\n")

    # Replace images with markdown-style references.
    for img in container.find_all("img"):
        src = img.get("data-src") or img.get("src")
        if not src:
            img.decompose()
            continue
        alt = (img.get("alt") or "").strip()
        replacement = f"\n\n![{alt}]({src})\n\n"
        img.replace_with(replacement)

    # Extract textual content.
    raw_text = container.get_text("\n", strip=True)
    lines = [line.strip() for line in raw_text.splitlines()]
    compacted = [line for line in lines if line]
    return "\n\n".join(compacted).strip()


def fetch_article_detail(url: str) -> ArticleDetail:
    """Download article content and convert it to markdown."""
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=10)
    response.raise_for_status()
    data_block = extract_data_block(response.text)
    origin = data_block.get("originContent") or {}
    markdown = html_to_markdown(origin.get("text") or "")
    publish_time = data_block.get("pubtime") or data_block.get("publish_time")
    return ArticleDetail(publish_time=publish_time, markdown=markdown)


def build_output_records(
    summaries: Iterable[ArticleSummary],
    cache: Dict[str, ArticleDetail],
    delay_seconds: float,
) -> List[Dict[str, str]]:
    """Combine list summaries with detailed article content."""
    output = []
    for summary in summaries:
        detail = cache.get(summary.article_id)
        if detail is None:
            try:
                detail = fetch_article_detail(summary.url)
                cache[summary.article_id] = detail
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[warn] failed to fetch detail for {summary.article_id}: {exc}",
                    file=sys.stderr,
                )
                cache[summary.article_id] = ArticleDetail(
                    publish_time=summary.publish_time,
                    markdown="",
                )
                detail = cache[summary.article_id]
            if delay_seconds:
                time.sleep(delay_seconds)
        publish_time = detail.publish_time or summary.publish_time or ""
        output.append(
            {
                "title": summary.title,
                "url": summary.url,
                "publish_time": publish_time,
                "content_markdown": detail.markdown,
            }
        )
    return output


def main(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Tencent News author articles and export JSON.",
    )
    parser.add_argument(
        "author",
        nargs="?",
        help="Author URL or suid (e.g. https://news.qq.com/omn/author/<suid>).",
    )
    parser.add_argument(
        "--author-file",
        default="qq_author.txt",
        help="Path to text file listing author URLs or suids (default: %(default)s).",
    )
    parser.add_argument(
        "--output",
        default="author_articles.json",
        help="Path to write JSON results (default: %(default)s).",
    )
    parser.add_argument(
        "--tab",
        default=None,
        help="Channel/tab id to fetch (default: author's default channel).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="Maximum number of article list pages to fetch (default: %(default)s).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Delay in seconds between requests (default: %(default)s).",
    )
    args = parser.parse_args(argv)

    authors_to_process = collect_authors(args.author, args.author_file)
    max_pages = max(1, args.max_pages)
    delay = max(args.delay, 0.0)

    results = []

    for original, author_id in authors_to_process:
        print(f"[info] processing author '{original}' -> '{author_id}'.")
        profile = fetch_author_profile(author_id)
        channel_cfg = profile.get("channel_config") or {}
        channel_list = channel_cfg.get("channel_list") or []
        default_tab = channel_cfg.get("defaultChannelId")
        tab_id = args.tab or default_tab or (
            channel_list[0]["channel_id"] if channel_list else "om_index"
        )
        print(f"[info] using tab '{tab_id}' for author '{author_id}'.")

        summaries = fetch_article_summaries(
            author_id=author_id,
            tab_id=tab_id,
            max_pages=max_pages,
            delay_seconds=delay,
        )
        print(f"[info] collected {len(summaries)} article summaries.")

        details_cache: Dict[str, ArticleDetail] = {}
        records = build_output_records(
            summaries=summaries,
            cache=details_cache,
            delay_seconds=delay,
        )
        results.append(
            {
                "author_id": author_id,
                "source": original,
                "tab_id": tab_id,
                "records": records,
            }
        )
        print(f"[info] gathered {len(records)} records for '{author_id}'.")

    if len(results) == 1:
        final_payload = results[0]["records"]
    else:
        final_payload = results

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(final_payload, fh, ensure_ascii=False, indent=2)
    print(f"[info] wrote data for {len(results)} author(s) to '{args.output}'.")


def collect_authors(
    cli_author: Optional[str],
    author_file: str,
) -> List[Tuple[str, str]]:
    """Return list of (raw_input, author_id) pairs to process."""
    if cli_author:
        return [(cli_author, parse_author_id(cli_author))]

    path = Path(author_file)
    if not path.exists():
        raise FileNotFoundError(
            f"No author input provided and file '{author_file}' does not exist.",
        )

    authors: List[Tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().lstrip("\ufeff")
        if not cleaned or cleaned.startswith("#"):
            continue
        authors.append((cleaned, parse_author_id(cleaned)))

    if not authors:
        raise RuntimeError(
            f"No valid authors found in '{author_file}'. Ensure it has one entry per line.",
        )
    return authors


if __name__ == "__main__":
    main()
