#!/usr/bin/env python3
"""Scrape Toutiao authors listed in a file, fetch article contents, and push to Supabase."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional, Set

from src.adapters.http_toutiao import (
    DEFAULT_LIMIT,
    SUPABASE_ENV_DEFAULT,
    build_supabase_config,
    fetch_article_records,
    fetch_existing_article_ids,
    fetch_feed_items,
    load_author_tokens,
    load_env_file,
    upload_records_to_supabase,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Toutiao authors and fetch article content.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("author.txt"),
        help="Path to file containing author profile URLs or tokens (default: author.txt).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help="Maximum number of feed items to collect in total (default: 100).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/toutiao_articles.json"),
        help="Path to write the collected article data as JSON.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Timeout in seconds for article content requests (default: 15).",
    )
    parser.add_argument(
        "--lang",
        default="zh-CN,zh;q=0.9",
        help="Accept-Language header value when requesting article content.",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run Playwright in headed mode instead of headless.",
    )
    parser.add_argument(
        "--supabase-env",
        type=Path,
        default=SUPABASE_ENV_DEFAULT,
        help="Path to the .env file containing Supabase credentials (default: .env.local).",
    )
    parser.add_argument(
        "--supabase-table",
        default="toutiao_articles",
        help="Supabase table name to create/populate (default: toutiao_articles).",
    )
    parser.add_argument(
        "--reset-supabase-table",
        action="store_true",
        help="Drop and recreate the Supabase table before inserting new data.",
    )
    parser.add_argument(
        "--skip-supabase-upload",
        action="store_true",
        help="Skip uploading data to Supabase even if credentials are present.",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    load_env_file(args.supabase_env)
    supabase_config = build_supabase_config(args)
    existing_ids: Optional[Set[str]] = None
    if supabase_config:
        existing_ids = fetch_existing_article_ids(supabase_config)
    try:
        entries = load_author_tokens(args.input)
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2

    limit: Optional[int]
    if args.limit <= 0:
        limit = None
    else:
        limit = args.limit

    feed_items = await fetch_feed_items(entries, limit, args.show_browser, existing_ids)
    if not feed_items:
        print("[warn] No feed items collected.", file=sys.stderr)

    records = fetch_article_records(feed_items, timeout=args.timeout, lang=args.lang, existing_ids=existing_ids)
    if not records:
        print("[warn] No article content fetched.", file=sys.stderr)

    uploaded = False
    if supabase_config and records:
        success = upload_records_to_supabase(records, supabase_config)
        if not success:
            return 5
        uploaded = True

    if uploaded:
        print(
            f"Fetched {len(records)} article(s) from {len(entries)} author(s). Uploaded to Supabase table {supabase_config.schema}.{supabase_config.table}",
            file=sys.stderr,
        )
    else:
        print(
            f"Fetched {len(records)} article(s) from {len(entries)} author(s). Supabase upload skipped.",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
