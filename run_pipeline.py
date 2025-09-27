#!/usr/bin/env python3
"""One-stop pipeline for the Supabase-based Toutiao workflow."""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent
TOOLS_DIR = REPO_ROOT / "tools"

DEFAULT_AUTHOR_LIST = TOOLS_DIR / "author.txt"
DEFAULT_SCRAPE_OUTPUT = REPO_ROOT / "data" / "toutiao_articles.json"
DEFAULT_KEYWORDS_PATH = REPO_ROOT / "education_keywords.txt"
DEFAULT_EXPORT_PATH = REPO_ROOT / "outputs" / "high_correlation_summaries.txt"
DEFAULT_SUPABASE_ENV = REPO_ROOT / ".env.local"


def run_step(name: str, cmd: List[str]) -> None:
    print(f"\n=== {name} ===")
    print(" ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"Step '{name}' failed with exit code {result.returncode}")


def positive_or_none(value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    return value if value > 0 else None


def resolve_relative(path: Path) -> Path:
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Supabase Toutiao pipeline end-to-end.")
    parser.add_argument("--skip-scrape", action="store_true", help="Skip scraping Toutiao authors")
    parser.add_argument("--scrape-input", type=Path, default=DEFAULT_AUTHOR_LIST, help="Author list for the scraper")
    parser.add_argument("--scrape-limit", type=int, default=150, help="Max feed items to collect (<=0 means no limit)")
    parser.add_argument("--scrape-output", type=Path, default=DEFAULT_SCRAPE_OUTPUT, help="JSON output path for scraped articles")
    parser.add_argument("--scrape-timeout", type=int, default=15, help="Timeout (seconds) when fetching article content")
    parser.add_argument("--scrape-lang", type=str, default=None, help="Override Accept-Language header")
    parser.add_argument("--scrape-show-browser", action="store_true", help="Run scraper with a visible browser window")
    parser.add_argument("--scrape-supabase-env", type=Path, default=DEFAULT_SUPABASE_ENV, help="Supabase credential file passed to the scraper")
    parser.add_argument("--scrape-supabase-table", type=str, default="toutiao_articles", help="Supabase table for storing scraped articles")
    parser.add_argument("--scrape-reset-table", action="store_true", help="Drop and recreate the Supabase table before inserting")
    parser.add_argument("--scrape-skip-upload", action="store_true", help="Skip uploading scraped data to Supabase")
    parser.add_argument("--skip-summary", action="store_true", help="Skip summarization step")
    parser.add_argument("--summary-keywords", type=Path, default=DEFAULT_KEYWORDS_PATH, help="Keywords file for summarization filtering")
    parser.add_argument("--summary-limit", type=int, default=0, help="Limit the number of articles to summarize (0 means no limit)")
    parser.add_argument("--summary-concurrency", type=int, default=5, help="Worker threads for summarization")
    parser.add_argument("--skip-score", action="store_true", help="Skip scoring step")
    parser.add_argument("--score-limit", type=int, default=0, help="Limit the number of summaries to score (0 means no limit)")
    parser.add_argument("--score-concurrency", type=int, default=5, help="Worker threads for scoring")
    parser.add_argument("--skip-export", action="store_true", help="Skip exporting high-correlation summaries")
    parser.add_argument("--export-min-score", type=int, default=60, help="Minimum correlation score required for export")
    parser.add_argument("--export-report-tag", type=str, default=None, help="Report tag recorded in Supabase history (e.g. 2025-09-27-ZB)")
    parser.add_argument("--export-output", type=Path, default=DEFAULT_EXPORT_PATH, help="Destination file for exported summaries")
    parser.add_argument("--export-skip-exported", action=argparse.BooleanOptionalAction, default=True, help="Skip items already exported with the same tag")
    parser.add_argument("--export-record-history", action=argparse.BooleanOptionalAction, default=True, help="Record export history in Supabase")
    return parser.parse_args()


def build_scrape_command(
    python_exec: str,
    input_path: Path,
    limit: Optional[int],
    output_path: Path,
    timeout: Optional[int],
    lang: Optional[str],
    show_browser: bool,
    supabase_env: Path,
    supabase_table: Optional[str],
    reset_table: bool,
    skip_upload: bool,
) -> List[str]:
    cmd = [python_exec, str(TOOLS_DIR / "toutiao_scraper.py"), "--input", str(input_path)]
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    cmd.extend(["--output", str(output_path)])
    if timeout is not None:
        cmd.extend(["--timeout", str(timeout)])
    if lang:
        cmd.extend(["--lang", lang])
    if show_browser:
        cmd.append("--show-browser")
    if supabase_env:
        cmd.extend(["--supabase-env", str(supabase_env)])
    if supabase_table:
        cmd.extend(["--supabase-table", supabase_table])
    if reset_table:
        cmd.append("--reset-supabase-table")
    if skip_upload:
        cmd.append("--skip-supabase-upload")
    return cmd


def build_summary_command(
    python_exec: str,
    keywords_path: Path,
    limit: Optional[int],
    concurrency: int,
) -> List[str]:
    cmd = [python_exec, str(TOOLS_DIR / "summarize_supabase.py"), "--keywords", str(keywords_path)]
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    cmd.extend(["--concurrency", str(max(1, concurrency))])
    return cmd


def build_score_command(
    python_exec: str,
    limit: Optional[int],
    concurrency: int,
) -> List[str]:
    cmd = [python_exec, str(TOOLS_DIR / "score_correlation_supabase.py")]
    cmd.extend(["--concurrency", str(max(1, concurrency))])
    if limit is not None:
        cmd.extend(["--limit", str(limit)])
    return cmd


def build_export_command(
    python_exec: str,
    min_score: int,
    report_tag: str,
    output_path: Path,
    skip_exported: bool,
    record_history: bool,
) -> List[str]:
    cmd = [
        python_exec,
        str(TOOLS_DIR / "export_high_correlation_supabase.py"),
        "--min-score",
        str(min_score),
        "--report-tag",
        report_tag,
        "--output",
        str(output_path),
    ]
    if not skip_exported:
        cmd.append("--no-skip-exported")
    if not record_history:
        cmd.append("--no-record-history")
    return cmd


def main() -> None:
    args = parse_args()
    python_exec = sys.executable

    scrape_input = resolve_relative(args.scrape_input)
    scrape_output = resolve_relative(args.scrape_output)
    supabase_env = resolve_relative(args.scrape_supabase_env)
    keywords_path = resolve_relative(args.summary_keywords)
    export_output = resolve_relative(args.export_output)

    scrape_limit = positive_or_none(args.scrape_limit)
    summary_limit = positive_or_none(args.summary_limit)
    score_limit = positive_or_none(args.score_limit)
    scrape_timeout = positive_or_none(args.scrape_timeout)

    if not args.skip_scrape:
        run_step(
            "Scrape Toutiao authors",
            build_scrape_command(
                python_exec,
                scrape_input,
                scrape_limit,
                scrape_output,
                scrape_timeout,
                args.scrape_lang,
                args.scrape_show_browser,
                supabase_env,
                args.scrape_supabase_table,
                args.scrape_reset_table,
                args.scrape_skip_upload,
            ),
        )

    if not args.skip_summary:
        run_step(
            "Summarize Supabase articles",
            build_summary_command(
                python_exec,
                keywords_path,
                summary_limit,
                args.summary_concurrency,
            ),
        )

    if not args.skip_score:
        run_step(
            "Score summary correlation",
            build_score_command(
                python_exec,
                score_limit,
                args.score_concurrency,
            ),
        )

    if not args.skip_export:
        export_report_tag = args.export_report_tag
        if export_report_tag is None:
            today_prefix = datetime.now().strftime("%Y-%m-%d")
            default_tag = f"{today_prefix}-ZB"
            print(f"Default report tag: {default_tag}")
            prompt = "Press Enter to use the default, or type a suffix (e.g. AM) to build YYYY-MM-DD-suffix: "
            user_input = input(prompt).strip()
            if user_input:
                export_report_tag = user_input if "-" in user_input else f"{today_prefix}-{user_input}"
            else:
                export_report_tag = default_tag
        run_step(
            "Export high correlation summaries",
            build_export_command(
                python_exec,
                args.export_min_score,
                export_report_tag,
                export_output,
                args.export_skip_exported,
                args.export_record_history,
            ),
        )

    print("\nPipeline completed.")


if __name__ == "__main__":
    main()
