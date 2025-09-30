#!/usr/bin/env python3
"""Deprecated shim: call the new summarize worker entry point."""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path
from typing import Optional, Sequence

from src.workers import summarize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="[deprecated] Summarize Toutiao articles via Supabase pipeline")
    parser.add_argument("--keywords", type=Path, default=None, help="Optional keywords file (defaults to config settings)")
    parser.add_argument("--limit", type=int, default=50, help="Number of articles to summarize")
    parser.add_argument("--concurrency", type=int, default=None, help="Maximum worker threads")
    parser.add_argument("--reset-cursor", action="store_true", help="Unused placeholder maintained for compatibility")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    warnings.warn(
        "tools/summarize_supabase.py is deprecated; use `python run_pipeline.py summarize` instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.reset_cursor:
        warnings.warn("--reset-cursor is ignored in the new worker-based pipeline.", stacklevel=2)

    summarize.run(
        limit=args.limit if args.limit and args.limit > 0 else 50,
        concurrency=args.concurrency,
        keywords_path=args.keywords,
    )


if __name__ == "__main__":
    main()
