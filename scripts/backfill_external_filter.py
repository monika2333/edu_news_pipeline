from __future__ import annotations

import argparse
from typing import Optional
from datetime import date

from src.adapters.db import get_adapter


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def backfill_external_filter(*, batch_size: int, limit: Optional[int], dry_run: bool, since_date: Optional[date] = None) -> int:
    adapter = get_adapter()
    total = 0

    # Dry-run: fetch once, de-duplicate, print, and exit to avoid
    # repeating the same batch without state changes.
    if dry_run:
        fetch_size = batch_size
        if limit is not None:
            fetch_size = min(batch_size, max(limit, 0))

        candidates = adapter.fetch_external_backfill_candidates(fetch_size, since_date=since_date)
        seen = set()
        ids_unique = []
        for row in candidates:
            aid = str(row.get("article_id") or "")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            ids_unique.append(aid)

        if ids_unique:
            print(f"[dry-run] would reset {len(ids_unique)} items:", ", ".join(ids_unique))
        else:
            print("[dry-run] no candidates matched the filter")

        print(f"[backfill] completed. total matched={len(ids_unique)}, dry_run=True")
        return len(ids_unique)

    # Real run: loop batches until exhausted; updates exclude them next pass
    while True:
        fetch_size = batch_size
        if limit is not None:
            remaining = max(limit - total, 0)
            if remaining <= 0:
                break
            fetch_size = min(fetch_size, remaining)

        candidates = adapter.fetch_external_backfill_candidates(fetch_size, since_date=since_date)
        if not candidates:
            break

        article_ids = [str(row.get("article_id")) for row in candidates if row.get("article_id")]
        if not article_ids:
            break

        updated = adapter.reset_external_filter_pending(article_ids)
        print(f"[backfill] reset {updated} items -> pending_external_filter")

        total += len(article_ids)
        if limit is not None and total >= limit:
            break

    print(f"[backfill] completed. total matched={total}, dry_run={dry_run}")
    return total


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Backfill external filter status for historical summaries")
    parser.add_argument("--batch-size", type=_positive_int, default=200, help="Rows to process per batch (default: 200)")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Optional cap on total processed rows")
    parser.add_argument("--dry-run", action="store_true", help="Inspect affected rows without updating them")
    parser.add_argument("--since", type=str, default=None, help="Only backfill rows with publish_time_iso date >= YYYY-MM-DD")
    args = parser.parse_args(argv)
    since_value: Optional[date] = None
    if args.since:
        try:
            since_value = date.fromisoformat(args.since)
        except Exception:
            raise SystemExit("--since must be in YYYY-MM-DD format, e.g. 2025-10-27")

    backfill_external_filter(batch_size=args.batch_size, limit=args.limit, dry_run=args.dry_run, since_date=since_value)


if __name__ == "__main__":
    main()
