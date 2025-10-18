from __future__ import annotations

import argparse
from pathlib import Path

from src.workers.crawl_sources import run as crawl_sources
from src.workers.export_brief import run as export_brief
from src.workers.geo_tag import run as geo_tag
from src.workers.repair_missing_content import run as repair_missing
from src.workers.score import run as score_summaries
from src.workers.summarize import run as summarize_articles
from src.workers.hash_primary import run as hash_primary


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _add_crawl(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("crawl", help="Collect fresh articles from configured sources")
    parser.add_argument("--limit", type=_positive_int, default=5000, help="Max number of feed items to ingest (across sources)")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional worker concurrency override")
    parser.add_argument("--sources", type=str, default="toutiao", help="Comma-separated sources, e.g. 'toutiao,chinanews,gmw'")
    parser.add_argument("--pages", type=_positive_int, default=None, help="Optional pages per paginated source (e.g., ChinaNews)")


def _add_repair(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("repair", help="Fetch article bodies for rows missing content")
    parser.add_argument("--limit", type=_positive_int, default=100, help="Max number of articles to repair")


def _add_summarize(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("summarize", help="Generate summaries for pending articles")
    parser.add_argument("--limit", type=_positive_int, default=500, help="Max number of pending summaries to process")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional worker concurrency override")
    parser.add_argument("--keywords", type=Path, default=None, help="(Deprecated) keywords now handled in crawl; kept for CLI compatibility")


def _add_hash_primary(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "hash-primary",
        help="Compute content hashes/SimHash for filtered articles and assign primary/duplicate groups",
    )
    parser.add_argument("--limit", type=_positive_int, default=200, help="Max number of filtered articles to process")


def _add_score(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("score", help="Score relevance for primary articles")
    parser.add_argument("--limit", type=_positive_int, default=500, help="Max number of summaries to score")
    parser.add_argument("--concurrency", type=_positive_int, default=None, help="Optional worker concurrency override")


def _add_export(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("export", help="Export high scoring summaries")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Max number of summaries to export")
    parser.add_argument("--date", type=str, default=None, help="Report date (YYYY-MM-DD). Defaults to today")
    parser.add_argument("--report-tag", type=str, default=None, help="Explicit report tag identifier")
    parser.add_argument("--min-score", type=_positive_int, default=60, help="Minimum score to include")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edu-news", description="Edu news pipeline controller")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_crawl(subparsers)
    _add_repair(subparsers)
    _add_hash_primary(subparsers)
    _add_summarize(subparsers)
    _add_score(subparsers)
    _add_export(subparsers)
    _add_geo_tag(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
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
    elif command == "score":
        score_summaries(limit=args.limit, concurrency=args.concurrency)
    elif command == "export":
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
    else:
        parser.error(f"Unknown command: {command}")


__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    main()
