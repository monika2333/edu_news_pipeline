from __future__ import annotations

import argparse
from typing import Callable

from src.workers.crawl_toutiao import run as crawl_toutiao
from src.workers.export_brief import run as export_brief
from src.workers.score import run as score_summaries
from src.workers.summarize import run as summarize_articles


def _add_crawl(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("crawl", help="Collect fresh Toutiao articles")
    parser.add_argument("--limit", type=int, default=50, help="Max number of feed items to ingest")


def _add_summarize(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("summarize", help="Generate summaries for pending articles")
    parser.add_argument("--limit", type=int, default=50, help="Max number of articles to summarize")


def _add_score(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("score", help="Score correlation for summarized articles")
    parser.add_argument("--limit", type=int, default=100, help="Max number of summaries to score")


def _add_export(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("export", help="Export high scoring summaries")
    parser.add_argument("--limit", type=int, default=None, help="Max number of items to export")
    parser.add_argument("--date", type=str, default=None, help="Report date (YYYY-MM-DD). Defaults to today")
    parser.add_argument("--min-score", type=int, default=60, help="Minimum correlation score to include")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edu-news", description="Edu news pipeline controller")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_crawl(subparsers)
    _add_summarize(subparsers)
    _add_score(subparsers)
    _add_export(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    command = args.command
    if command == "crawl":
        crawl_toutiao(limit=args.limit)
    elif command == "summarize":
        summarize_articles(limit=args.limit)
    elif command == "score":
        score_summaries(limit=args.limit)
    elif command == "export":
        export_brief(limit=args.limit, date=args.date, min_score=args.min_score)
    else:
        parser.error(f"Unknown command: {command}")


__all__ = ["build_parser", "main"]

