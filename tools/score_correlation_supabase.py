#!/usr/bin/env python3
"""Deprecated shim: call the new scoring worker."""
from __future__ import annotations

import argparse
import warnings
from typing import Optional, Sequence

from src.workers import score


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="[deprecated] Score Supabase summaries for relevance")
    parser.add_argument("--concurrency", type=int, default=None, help="Maximum worker threads")
    parser.add_argument("--limit", type=int, default=100, help="Limit number of summaries to score")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    warnings.warn(
        "tools/score_correlation_supabase.py is deprecated; use `python run_pipeline.py score` instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    score.run(
        limit=args.limit if args.limit and args.limit > 0 else 100,
        concurrency=args.concurrency,
    )


if __name__ == "__main__":
    main()
