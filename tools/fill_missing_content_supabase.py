#!/usr/bin/env python3
"""Backfill missing content in Supabase raw_articles using Toutiao fetcher."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

try:
    from tools.supabase_adapter import ArticleInput, get_supabase_adapter
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from supabase_adapter import ArticleInput, get_supabase_adapter  # type: ignore

PARENT_DIR = Path(__file__).resolve().parents[1]
if str(PARENT_DIR) not in sys.path:
    sys.path.append(str(PARENT_DIR))
from tools import toutiao_fetch as tf


def is_bjd(url: Optional[str]) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return bool(parsed.netloc) and "bjd.com.cn" in parsed.netloc
    except Exception:
        return False


def build_article_input(target_hash: str, payload: dict) -> ArticleInput:
    title = payload.get("title")
    source = payload.get("source") or payload.get("detail_source")
    publish_time = payload.get("publish_time")
    try:
        publish_time_int = int(publish_time) if publish_time else None
    except Exception:
        publish_time_int = None
    return ArticleInput(
        article_id=payload.get("group_id") or payload.get("id") or target_hash,
        title=title,
        source=source,
        publish_time=publish_time_int,
        original_url=payload.get("url"),
        content=tf.html_to_markdown(payload.get("content") or ""),
        raw_payload=payload,
        metadata={"backfill": True},
    )


def backfill(limit: Optional[int], delay: float, timeout: int, lang: str) -> None:
    adapter = get_supabase_adapter()
    targets = adapter.iter_missing_content(limit)
    total = len(targets)
    ok = 0
    fail = 0
    for idx, target in enumerate(targets, start=1):
        url_or_id = target.original_url or target.article_hash
        if not url_or_id:
            fail += 1
            print(f"[{idx}/{total}] skip: missing URL and article hash")
            continue
        try:
            if target.original_url and is_bjd(target.original_url):
                data = tf.fetch_bjd(target.original_url, timeout=timeout, lang=lang)
            else:
                try:
                    article_id = tf.extract_article_id(url_or_id)
                except Exception:
                    if target.original_url:
                        data = tf.fetch_bjd(target.original_url, timeout=timeout, lang=lang)
                    else:
                        raise
                else:
                    data = tf.fetch_info(article_id, timeout=timeout, lang=lang)
            record = build_article_input(target.article_hash, data)
            adapter.upsert_article(record)
            ok += 1
            print(f"[{idx}/{total}] ok: {record.title or target.article_hash}")
        except Exception as exc:
            fail += 1
            print(f"[{idx}/{total}] error: {exc}")
        time.sleep(max(0.0, delay))
    print("--- Supabase Backfill Summary ---")
    print(f"processed: {total}, ok: {ok}, failed: {fail}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill missing Supabase article content")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of rows to process (0 = all)")
    parser.add_argument("--delay", type=float, default=1.0, help="Sleep seconds between requests")
    parser.add_argument("--timeout", type=int, default=15, help="Request timeout in seconds")
    parser.add_argument("--lang", default="zh-CN,zh;q=0.9", help="HTTP Accept-Language header")
    return parser.parse_args()


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args()
    backfill(args.limit if args.limit and args.limit > 0 else None, args.delay, args.timeout, args.lang)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
