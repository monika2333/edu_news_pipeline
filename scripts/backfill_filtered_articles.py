from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from src.adapters import db_postgres
from src.adapters.db import get_adapter
from src.config import get_settings, load_environment
from src.workers import log_info, log_summary

WORKER = "backfill_filtered"


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid datetime format: {value}") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("Datetime must include timezone information")
    return parsed


def _load_keywords(path: Path) -> List[str]:
    if not path.exists():
        return []
    keywords: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.strip()
        if token and not token.startswith("#") and token not in keywords:
            keywords.append(token)
    return keywords


def _contains_keywords(text: str, keywords: Sequence[str]) -> List[str]:
    if not keywords:
        return []
    lowered = text.lower()
    hits: List[str] = []
    for kw in keywords:
        if kw and kw.lower() in lowered and kw not in hits:
            hits.append(kw)
    return hits


def _read_article_ids(paths: Iterable[Path]) -> List[str]:
    ids: List[str] = []
    for path in paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            article_id = line.strip()
            if article_id and article_id not in ids:
                ids.append(article_id)
    return ids


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill filtered_articles from raw_articles using keyword matching."
    )
    parser.add_argument(
        "--since-updated",
        type=_parse_datetime,
        help="Filter raw_articles by updated_at >= timestamp (ISO 8601 with timezone).",
    )
    parser.add_argument(
        "--article-id",
        action="append",
        dest="article_ids",
        default=[],
        help="Specific article_id to backfill (can be used multiple times).",
    )
    parser.add_argument(
        "--article-id-file",
        action="append",
        dest="article_id_files",
        type=Path,
        default=[],
        help="Path to file containing article_id values (one per line).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Number of rows to process per batch (default: 200).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on total raw articles loaded.",
    )
    parser.add_argument(
        "--keywords-path",
        type=Path,
        default=None,
        help="Override path to keywords file (defaults to settings.keywords_path).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process and report statistics without writing to filtered_articles.",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    load_environment()
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    settings = get_settings()
    keywords_path = args.keywords_path or settings.keywords_path
    keywords = _load_keywords(Path(keywords_path))
    if not keywords:
        log_info(WORKER, f"No keywords found at {keywords_path}. Nothing to backfill.")
        return 0

    article_ids: Set[str] = set()
    if args.article_ids:
        article_ids.update(aid.strip() for aid in args.article_ids if aid)
    if args.article_id_files:
        article_ids.update(_read_article_ids(args.article_id_files))

    adapter = get_adapter()
    processed = 0
    matched = 0
    inserted = 0

    log_info(
        WORKER,
        f"Starting backfill with batch_size={args.batch_size}, limit={args.limit}, "
        f"since_updated={args.since_updated.isoformat() if args.since_updated else 'None'}, "
        f"keywords={len(keywords)}, dry_run={args.dry_run}",
    )

    rows_iter = adapter.iter_raw_articles_for_filtered_backfill(
        since=args.since_updated,
        article_ids=sorted(article_ids) if article_ids else None,
        batch_size=args.batch_size,
        limit=args.limit,
    )

    for batch in rows_iter:
        batch_payload: List[dict] = []
        for row in batch:
            processed += 1
            article_id = str(row.get("article_id") or "").strip()
            content = str(row.get("content_markdown") or "")
            if not article_id or not content.strip():
                continue
            hits = _contains_keywords(content, keywords)
            if not hits:
                continue
            matched += 1
            content_hash = row.get("content_hash")
            fingerprint = row.get("fingerprint")
            if not content_hash or not fingerprint:
                computed_hash, computed_fingerprint = db_postgres._compute_content_features(content)
                content_hash = content_hash or computed_hash
                fingerprint = fingerprint or computed_fingerprint
            payload = {
                "article_id": article_id,
                "primary_article_id": article_id,
                "keywords": hits,
                "title": row.get("title"),
                "source": row.get("source"),
                "publish_time": row.get("publish_time"),
                "publish_time_iso": row.get("publish_time_iso"),
                "url": row.get("url"),
                "content_markdown": content,
                "content_hash": content_hash,
                "fingerprint": fingerprint,
                "sentiment_label": row.get("sentiment_label"),
                "sentiment_confidence": row.get("sentiment_confidence"),
            }
            batch_payload.append(payload)

        if not batch_payload:
            continue

        if args.dry_run:
            log_info(WORKER, f"[dry-run] would upsert {len(batch_payload)} rows (running total matched={matched})")
        else:
            inserted += adapter.upsert_filtered_articles(batch_payload)
            log_info(
                WORKER,
                f"Upserted {len(batch_payload)} rows into filtered_articles "
                f"(running total inserted={inserted}, matched={matched})",
            )

        if args.limit and processed >= args.limit:
            break

    log_summary(WORKER, ok=inserted if not args.dry_run else matched, failed=0, skipped=processed - matched)
    log_info(
        WORKER,
        f"Backfill completed: processed={processed}, matched={matched}, "
        f"inserted={'dry-run' if args.dry_run else inserted}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
