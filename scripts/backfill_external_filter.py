from __future__ import annotations

import argparse
from typing import Optional

from src.adapters.db import get_adapter


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def backfill_external_filter(*, batch_size: int, limit: Optional[int], dry_run: bool) -> int:
    adapter = get_adapter()
    total = 0

    while True:
        fetch_size = batch_size
        if limit is not None:
            remaining = max(limit - total, 0)
            if remaining <= 0:
                break
            fetch_size = min(fetch_size, remaining)

        candidates = adapter.fetch_external_backfill_candidates(fetch_size)
        if not candidates:
            break

        article_ids = [str(row.get("article_id")) for row in candidates if row.get("article_id")]
        if not article_ids:
            break

        if dry_run:
            print(f"[dry-run] would reset {len(article_ids)} items:", ", ".join(article_ids))
        else:
            updated = adapter.reset_external_filter_pending(article_ids)
            print(f"[backfill] reset {updated} items → pending_external_filter")

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
    args = parser.parse_args(argv)

    backfill_external_filter(batch_size=args.batch_size, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
