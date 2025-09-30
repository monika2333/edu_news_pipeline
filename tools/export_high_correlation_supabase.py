#!/usr/bin/env python3
"""Deprecated shim: call the new export worker."""
from __future__ import annotations

import argparse
import warnings
from pathlib import Path
from typing import Optional, Sequence

from src.workers import export_brief


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="[deprecated] Export high-correlation summaries")
    parser.add_argument("--min-score", type=int, default=70, help="Minimum relevance score to export")
    parser.add_argument("--report-tag", type=str, default=None, help="Report tag identifier (e.g. 2024-09-30-AM)")
    parser.add_argument("--date", type=str, default=None, help="Optional date string (YYYY-MM-DD)")
    parser.add_argument("--skip-exported", action="store_true", default=True, help="Skip entries already exported (default)")
    parser.add_argument("--no-skip-exported", dest="skip_exported", action="store_false", help="Force include previously exported entries")
    parser.add_argument("--no-history", dest="record_history", action="store_false", help="Do not record export history")
    parser.add_argument("--output", type=Path, default=None, help="Override output file path")
    parser.set_defaults(skip_exported=True, record_history=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    warnings.warn(
        "tools/export_high_correlation_supabase.py is deprecated; use `python run_pipeline.py export` instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    parser = build_parser()
    args = parser.parse_args(argv)

    export_brief.run(
        date=args.date,
        min_score=args.min_score,
        report_tag=args.report_tag,
        skip_exported=args.skip_exported,
        record_history=args.record_history,
        output_base=args.output,
    )


if __name__ == "__main__":
    main()

