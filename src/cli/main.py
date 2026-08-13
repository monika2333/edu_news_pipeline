from __future__ import annotations

import argparse
import getpass
import os
import sys
import threading
import time
from pathlib import Path

from src.config import get_settings
from src.workers.crawl_sources import run as crawl_sources
from src.workers.enrich_summary import run as enrich_summaries
from src.workers.external_filter import run as run_external_filter
from src.workers.geo_classify import run as classify_geography
from src.workers.geo_tag import run as geo_tag
from src.workers.hash_primary import run as hash_primary
from src.workers.repair_missing_content import run as repair_missing
from src.workers.score import run as score_summaries
from src.workers.summarize import run as summarize_articles

_MANUAL_CLUSTER_WATCHDOG_SECONDS = 600


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _add_crawl(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("crawl", help="Collect fresh articles from configured sources")
    parser.add_argument("--limit", type=_positive_int, default=5000, help="Max number of feed items to ingest (across sources)")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional worker concurrency override")
    parser.add_argument("--sources", type=str, default="toutiao", help="Comma-separated sources, e.g. 'toutiao,tencent,chinanews'")
    parser.add_argument("--pages", type=_positive_int, default=None, help="Optional pages per paginated source (e.g., ChinaNews)")


def _add_repair(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("repair", help="Fetch article bodies for rows missing content")
    parser.add_argument("--limit", type=_positive_int, default=100, help="Max number of articles to repair")


def _add_summarize(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "summarize",
        help="Generate summaries for pending articles without post-summary enrichment",
    )
    parser.add_argument("--limit", type=_positive_int, default=2500, help="Max number of pending summaries to process")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional worker concurrency override")
    parser.add_argument("--keywords", type=Path, default=None, help="(Deprecated) keywords now handled in crawl; kept for CLI compatibility")


def _add_enrich_summary(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "enrich-summary",
        help="Run independent sentiment and source-detection requests for summaries",
    )
    parser.add_argument("--limit", type=_positive_int, default=2500, help="Max number of summaries to enrich")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional LLM worker concurrency override")


def _add_geo_classify(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "geo-classify",
        help="Classify geography using local matching and the Beijing LLM gate",
    )
    parser.add_argument("--limit", type=_positive_int, default=2500, help="Max number of summaries to classify")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional Beijing gate concurrency override")


def _add_hash_primary(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "hash-primary",
        help="Compute content hashes/SimHash for filtered articles and assign primary/duplicate groups",
    )
    parser.add_argument("--limit", type=_positive_int, default=5000, help="Max number of filtered articles to process")


def _add_score(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("score", help="Score relevance for primary articles")
    parser.add_argument("--limit", type=_positive_int, default=2500, help="Max number of summaries to score")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional worker concurrency override")


def _add_external_filter(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "external-filter",
        help="Run importance scoring after geographic classification",
    )
    parser.add_argument("--limit", type=_positive_int, default=2000, help="Max number of rows to process")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional concurrency override for LLM calls")


def _add_submission_dedup(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "submission-dedup",
        help="Compare current news with the recent submission archive",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="Optional maximum number of current news rows",
    )


def _add_backfill_submission_embeddings(
    subparsers: argparse._SubParsersAction,
) -> None:
    parser = subparsers.add_parser(
        "backfill-submission-embeddings",
        help="Fill missing submission archive embeddings",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=128,
        help="Embedding batch size (default: 128)",
    )



def _add_export(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("export", help="Export high scoring summaries")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Max number of summaries to export")
    parser.add_argument("--date", type=str, default=None, help="Report date (YYYY-MM-DD). Defaults to today")
    parser.add_argument("--report-tag", type=str, default=None, help="Explicit report tag identifier")
    parser.add_argument(
        "--min-score",
        type=_positive_int,
        default=get_settings().score_promotion_threshold,
        help="Minimum score to include (defaults to SCORE_PROMOTION_THRESHOLD)",
    )
    parser.add_argument("--skip-exported", action=argparse.BooleanOptionalAction, default=True, help="Skip items already exported in previous runs")
    parser.add_argument("--record-history", action=argparse.BooleanOptionalAction, default=True, help="Persist export metadata back to the database")
    parser.add_argument("--output", type=Path, default=None, help="Override output file path")


def _add_geo_tag(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("geo-tag", help="Backfill Beijing relevance tags for existing summaries")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Max number of summaries to process")
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=200,
        help="Number of rows to fetch per database batch",
    )


def _add_create_console_user(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "create-console-user",
        help="Create an administrator-managed console account",
    )
    parser.add_argument("--username", required=True, help="Unique login name")
    parser.add_argument("--display-name", required=True, help="Name shown in the console")
    parser.add_argument(
        "--role",
        choices=("admin", "duty_editor"),
        default="admin",
        help="Console role (default: admin)",
    )
    parser.add_argument(
        "--password-env",
        default=None,
        help="Read the initial password from this environment variable",
    )


def _add_cleanup_console_sessions(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "cleanup-console-sessions",
        help="Delete expired and long-revoked console sessions",
    )


def _add_generate_shifts(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "generate-shifts",
        help="Generate upcoming duty shifts from the weekly schedule",
    )
    parser.add_argument(
        "--days",
        type=_positive_int,
        default=14,
        help="Number of coverage days to ensure (default: 14)",
    )


def _add_refresh_manual_clusters(subparsers: argparse._SubParsersAction) -> None:
    subparsers.add_parser(
        "refresh-manual-clusters",
        help="Refresh the manual-review title clusters",
    )


def _read_initial_password(password_env: str | None) -> str:
    if password_env:
        password = os.getenv(password_env)
        if password is None:
            raise ValueError(f"Environment variable is not set: {password_env}")
        return password
    password = getpass.getpass("Initial password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise ValueError("Passwords do not match")
    return password


def _create_console_user(args: argparse.Namespace) -> None:
    from src.console.auth_service import create_console_user

    password = _read_initial_password(args.password_env)
    user = create_console_user(
        username=args.username,
        display_name=args.display_name,
        password=password,
        role=args.role,
    )
    print(f"Created console user {user['username']} ({user['role']})")


def _cleanup_console_sessions() -> None:
    from src.console.auth_service import cleanup_expired_sessions

    deleted = cleanup_expired_sessions()
    print(f"Deleted {deleted} expired console sessions")


def _generate_shifts(days: int) -> None:
    from src.console.shifts_service import generate_shifts

    result = generate_shifts(days=days)
    print(
        "Generated duty shifts: "
        f"{result['inserted']} inserted, {result['requested']} requested"
    )


def _watchdog(seconds: int) -> None:
    def kill() -> None:
        time.sleep(seconds)
        print(
            "TIMEOUT: refresh-manual-clusters exceeded limit",
            file=sys.stderr,
            flush=True,
        )
        os._exit(3)

    threading.Thread(target=kill, daemon=True).start()


def _refresh_manual_clusters() -> int:
    _watchdog(_MANUAL_CLUSTER_WATCHDOG_SECONDS)
    from src.console.manual_filter_service import trigger_clustering

    result = trigger_clustering()
    if result["refreshed"]:
        print("Manual clusters: refreshed")
        return 0
    print("Manual clusters: skipped (already running)")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edu-news", description="Edu news pipeline controller")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_crawl(subparsers)
    _add_repair(subparsers)
    _add_hash_primary(subparsers)
    _add_summarize(subparsers)
    _add_enrich_summary(subparsers)
    _add_geo_classify(subparsers)
    _add_score(subparsers)
    _add_external_filter(subparsers)
    _add_submission_dedup(subparsers)
    _add_backfill_submission_embeddings(subparsers)
    _add_export(subparsers)
    _add_geo_tag(subparsers)
    _add_create_console_user(subparsers)
    _add_cleanup_console_sessions(subparsers)
    _add_generate_shifts(subparsers)
    _add_refresh_manual_clusters(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command
    if command == "crawl":
        crawl_sources(limit=args.limit, concurrency=args.concurrency, sources=args.sources, pages=args.pages)
    elif command == "repair":
        repair_missing(limit=args.limit)
    elif command == "hash-primary":
        hash_primary(limit=args.limit)
    elif command == "summarize":
        summarize_articles(limit=args.limit, concurrency=args.concurrency, keywords_path=args.keywords)
    elif command == "enrich-summary":
        enrich_summaries(limit=args.limit, concurrency=args.concurrency)
    elif command == "geo-classify":
        classify_geography(limit=args.limit, concurrency=args.concurrency)
    elif command == "score":
        score_summaries(limit=args.limit, concurrency=args.concurrency)
    elif command == "external-filter":
        run_external_filter(limit=args.limit, concurrency=args.concurrency)
    elif command == "submission-dedup":
        from src.workers.submission_dedup import run as run_submission_dedup

        run_submission_dedup(limit=args.limit)
    elif command == "backfill-submission-embeddings":
        from src.workers.submission_dedup import backfill_archive_embeddings

        count = backfill_archive_embeddings(batch_size=args.batch_size)
        print(f"Embedded {count} submission archive items")
    elif command == "export":
        from src.workers.export_brief import run as export_brief

        export_brief(
            limit=args.limit,
            date=args.date,
            min_score=args.min_score,
            report_tag=args.report_tag,
            skip_exported=args.skip_exported,
            record_history=args.record_history,
            output_base=args.output,
        )
    elif command == "geo-tag":
        geo_tag(limit=args.limit, batch_size=args.batch_size)
    elif command == "create-console-user":
        _create_console_user(args)
    elif command == "cleanup-console-sessions":
        _cleanup_console_sessions()
    elif command == "generate-shifts":
        _generate_shifts(args.days)
    elif command == "refresh-manual-clusters":
        return _refresh_manual_clusters()
    else:
        parser.error(f"Unknown command: {command}")
    return 0


__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
