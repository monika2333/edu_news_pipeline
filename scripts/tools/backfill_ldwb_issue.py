from __future__ import annotations
import argparse
import re
import sys
from datetime import datetime, timezone
from typing import List, Sequence
from urllib.parse import urljoin

import requests
import urllib3
from bs4 import BeautifulSoup

from src.adapters.db_postgres import PostgresAdapter
from src.adapters.http_laodongwubao import (
    parse_article,
    article_to_feed_row,
    article_to_detail_row,
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


def extract_article_links(page_html: str, page_url: str) -> List[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    links: List[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        lower = href.lower()
        if "content_" not in lower or not lower.endswith(".htm"):
            continue
        full = urljoin(page_url, href)
        if full in seen:
            continue
        seen.add(full)
        links.append(full)
    return links


def discover_issue_nodes(start_html: str, start_url: str) -> List[str]:
    """Discover all node_X.htm URLs for an issue based on a node/index page.

    Strategy:
    - Parse anchors for hrefs like node_\d+.htm; join against start_url.
    - Always include start_url itself if it looks like a node_*.htm.
    - Fallback: caller may choose to probe sequentially if nothing found.
    """
    soup = BeautifulSoup(start_html, "html.parser")
    issue_dir_match = re.search(r"/content/\d{4}-\d{2}/\d{2}/", start_url)
    issue_dir = issue_dir_match.group(0) if issue_dir_match else None
    node_urls: List[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href:
            continue
        if not re.search(r"node_\d+\.htm$", href, flags=re.IGNORECASE):
            continue
        full = urljoin(start_url, href)
        if issue_dir and issue_dir not in full:
            # Skip links pointing to a different issue date (e.g., prev/next issue)
            continue
        if full in seen:
            continue
        seen.add(full)
        node_urls.append(full)

    # Ensure current page included if it's a node_*.htm
    if re.search(r"node_\d+\.htm$", start_url, flags=re.IGNORECASE):
        if start_url not in seen:
            node_urls.insert(0, start_url)
            seen.add(start_url)

    return node_urls


def persist_records(records: Sequence, *, dry_run: bool) -> int:
    if not records:
        return 0
    if dry_run:
        print(f"Dry-run: would persist {len(records)} records")
        return 0

    adapter = PostgresAdapter()
    try:
        existing = adapter.get_existing_raw_article_ids()
    except Exception as exc:
        print(f"ERROR: unable to get existing article ids: {exc}")
        return -1

    feed_rows = []
    detail_rows = []
    now = datetime.now(timezone.utc)
    seen_ids = set()
    for rec in records:
        aid = (rec.article_id or "").strip()
        if not aid or aid in seen_ids or aid in existing:
            continue
        seen_ids.add(aid)
        feed_rows.append(article_to_feed_row(rec, fetched_at=now))
        detail_rows.append(article_to_detail_row(rec, detail_fetched_at=now))

    if not feed_rows:
        print("All records already existed; nothing new to insert.")
        return 0

    try:
        adapter.upsert_raw_feed_rows(feed_rows)
        adapter.update_raw_article_details(detail_rows)
    except Exception as exc:
        print(f"ERROR: DB write failed: {exc}")
        return -2

    print(f"Inserted/updated {len(feed_rows)} records.")
    return len(feed_rows)


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser(
        description=(
            "Backfill Labor Daily (LDWB) by issue page: "
            "auto-discovers all node_X.htm pages and persists articles."
        )
    )
    p.add_argument(
        "url",
        help=(
            "Issue page URL (node_X.htm or index.htm), e.g. "
            "https://ldwb.workerbj.cn/content/2025-11/14/node_2.htm"
        ),
    )
    p.add_argument("--timeout", type=float, default=20.0)
    p.add_argument("--verify", action="store_true", help="Verify TLS certs (default off)")
    p.add_argument("--dry-run", action="store_true", help="Only print actions, no DB writes")
    p.add_argument(
        "--probe-max",
        type=int,
        default=0,
        help=(
            "If no node links are found, optionally probe node_1..node_N under the "
            "same directory (0=disabled)."
        ),
    )
    args = p.parse_args(argv)

    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    if not args.verify:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Fetch starting page
    print(f"Fetch issue page: {args.url}")
    resp = sess.get(args.url, timeout=args.timeout, verify=args.verify)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    start_html = resp.text

    # Discover all node pages for this issue
    node_urls = discover_issue_nodes(start_html, args.url)

    # Optional fallback probing
    if not node_urls and args.probe_max and args.probe_max > 0:
        base_dir = args.url.rsplit("/", 1)[0] + "/"
        for i in range(1, args.probe_max + 1):
            test_url = urljoin(base_dir, f"node_{i}.htm")
            try:
                r = sess.get(test_url, timeout=args.timeout, verify=args.verify)
                if r.status_code == 200 and len((r.text or "").strip()) > 0:
                    node_urls.append(test_url)
            except Exception:
                continue
        # Deduplicate while keeping order
        seen = set()
        node_urls = [u for u in node_urls if not (u in seen or seen.add(u))]

    if not node_urls:
        # Treat the provided URL itself as the only page
        print("No node links discovered; fallback to single page backfill.")
        node_urls = [args.url]

    print(f"Discovered node pages: {len(node_urls)}")
    for u in node_urls:
        print(f" - {u}")

    # Crawl each node page and collect article links
    all_article_links: List[str] = []
    seen_links = set()
    for node in node_urls:
        try:
            r = sess.get(node, timeout=args.timeout, verify=args.verify)
            r.raise_for_status()
            r.encoding = "utf-8"
            links = extract_article_links(r.text, node)
            for link in links:
                if link in seen_links:
                    continue
                seen_links.add(link)
                all_article_links.append(link)
        except Exception as exc:
            print(f"WARN failed to fetch node page {node}: {exc}")

    if not all_article_links:
        print("No article links found across node pages.")
        return 1
    print(f"Discovered {len(all_article_links)} article links in total.")

    # Fetch and parse each article
    records = []
    for link in all_article_links:
        try:
            r = sess.get(link, timeout=args.timeout, verify=args.verify)
            r.raise_for_status()
            r.encoding = "utf-8"
            record = parse_article(r.text, link, page_name="BACKFILL")
            records.append(record)
            print(f"Parsed: {record.article_id}")
        except Exception as exc:
            print(f"WARN failed to parse {link}: {exc}")

    if not records:
        print("No records parsed; nothing to persist.")
        return 2

    result = persist_records(records, dry_run=args.dry_run)
    return 0 if result >= 0 else 4


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
