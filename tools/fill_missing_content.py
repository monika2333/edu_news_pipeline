#!/usr/bin/env python3
"""
Backfill missing `content` in articles.sqlite3 by fetching from source
using the existing tools/toutiao_fetch.py logic (no third-party deps).

- Selects rows where content is NULL or empty
- For each row, chooses input as `original_url` if present; else `article_id`
- Fetches Toutiao or BJD JSON and upserts via the same save_to_sqlite()

Usage:
  python tools/fill_missing_content.py --db articles.sqlite3 [--limit 100] [--delay 1.0]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from typing import Optional
from urllib.parse import urlparse


def load_targets(conn: sqlite3.Connection, limit: Optional[int] = None):
    sql = (
        "SELECT id, article_id, original_url FROM articles "
        "WHERE content IS NULL OR TRIM(content) = '' "
        "ORDER BY id ASC"
    )
    if limit is not None and limit > 0:
        sql += f" LIMIT {int(limit)}"
    cur = conn.execute(sql)
    return [(int(r[0]), (r[1] or ""), (r[2] or "")) for r in cur]


def is_bjd(url: str) -> bool:
    try:
        u = urlparse(url)
        return bool(u.netloc) and ("bjd.com.cn" in u.netloc)
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill missing article content via network fetch")
    ap.add_argument('--db', default='articles.sqlite3', help='SQLite DB file path')
    ap.add_argument('--limit', type=int, default=0, help='Max rows to process (0 = all)')
    ap.add_argument('--delay', type=float, default=1.0, help='Sleep seconds between requests')
    ap.add_argument('--timeout', type=int, default=15, help='Network timeout seconds')
    ap.add_argument('--lang', default='zh-CN,zh;q=0.9')
    args = ap.parse_args(argv)

    # Lazy import to avoid overhead if only listing
    try:
        # When executed as `python tools/fill_missing_content.py`, cwd in sys.path points to this folder.
        # So import sibling module directly.
        import toutiao_fetch as tf  # type: ignore
    except Exception:
        # Fallback: try as a package from repo root if available
        from tools import toutiao_fetch as tf  # type: ignore

    db_path = args.db
    conn = sqlite3.connect(db_path)
    try:
        rows = load_targets(conn, args.limit if args.limit and args.limit > 0 else None)
    finally:
        conn.close()

    total = 0
    ok = 0
    fail = 0
    for rid, aid, url in rows:
            total += 1
            target = url or aid
            if not target:
                fail += 1
                print(f"[{rid}] skip: no url or article_id")
                continue
            try:
                if url and is_bjd(url):
                    data = tf.fetch_bjd(url, timeout=args.timeout, lang=args.lang)
                else:
                    # If target looks like URL, extract id; else assume it's id
                    try:
                        article_id = tf.extract_article_id(target)
                    except Exception:
                        # if cannot extract id from URL, try BJD path; else give up
                        if url:
                            data = tf.fetch_bjd(url, timeout=args.timeout, lang=args.lang)
                        else:
                            raise
                    else:
                        data = tf.fetch_info(article_id, timeout=args.timeout, lang=args.lang)
                # Ensure source overrides with the most authoritative value
                if data is not None:
                    src = data.get('source') or data.get('detail_source')
                    if src:
                        data['source'] = src
                # Save into DB (upsert)
                tf.save_to_sqlite(db_path, data)
                ok += 1
                print(f"[{rid}] ok: {data.get('title','').strip()[:40]}")
            except Exception as e:
                fail += 1
                print(f"[{rid}] error: {e}")
            time.sleep(max(0.0, args.delay))
    print('--- Backfill Summary ---')
    print(f'processed: {total}, ok: {ok}, failed: {fail}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
