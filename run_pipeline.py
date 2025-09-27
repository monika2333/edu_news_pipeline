#!/usr/bin/env python3
"""One-stop pipeline for either SQLite or Supabase backends."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent
TOOLS_DIR = REPO_ROOT / "tools"
DEFAULT_DB_PATH = REPO_ROOT / "articles.sqlite3"
DEFAULT_KEYWORDS_PATH = REPO_ROOT / "education_keywords.txt"
DEFAULT_EXPORT_PATH = REPO_ROOT / "outputs" / "high_correlation_summaries.txt"

BACKEND_SQLITE = "sqlite"
BACKEND_SUPABASE = "supabase"
BACKEND_AUTO = "auto"


def run_step(name: str, cmd: List[str]) -> None:
    print(f"\n=== {name} ===")
    print(" ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"Step '{name}' failed with exit code {result.returncode}")


def detect_backend(requested: str) -> str:
    if requested == BACKEND_SQLITE:
        return BACKEND_SQLITE
    if requested == BACKEND_SUPABASE:
        return BACKEND_SUPABASE
    # auto detection
    if os.getenv("SUPABASE_URL") and (
        os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    ):
        return BACKEND_SUPABASE
    return BACKEND_SQLITE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the news processing pipeline")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="主 SQLite 数据库路径 (SQLite 模式有效)")
    parser.add_argument("--keywords", type=Path, default=DEFAULT_KEYWORDS_PATH, help="关键词文件路径")
    parser.add_argument("--export-output", type=Path, default=DEFAULT_EXPORT_PATH, help="高相关度摘要导出文件")
    parser.add_argument("--import-src", type=Path, default=REPO_ROOT / "AuthorFetch", help="AuthorFetch 源目录")
    parser.add_argument("--fill-limit", type=int, default=0, help="回填正文的最大数量 (0=全部)")
    parser.add_argument("--fill-delay", type=float, default=1.0, help="回填正文的请求间隔秒")
    parser.add_argument("--fill-timeout", type=int, default=15, help="回填正文的超时时间")
    parser.add_argument("--summarize-limit", type=int, default=0, help="摘要阶段的最大处理数量 (0=全部)")
    parser.add_argument("--summarize-concurrency", type=int, default=0, help="摘要阶段并发线程数")
    parser.add_argument("--score-concurrency", type=int, default=0, help="相关度打分并发数")
    parser.add_argument("--min-score", type=int, default=60, help="导出摘要的最低相关度分数")
    parser.add_argument("--cleanup-apply", action=argparse.BooleanOptionalAction, default=True, help="清理阶段是否实际删除文件")
    parser.add_argument("--allow-empty-content", action="store_true", help="清理时允许正文为空仍删除")
    parser.add_argument("--skip-import", action="store_true", help="跳过导入阶段")
    parser.add_argument("--skip-fill", action="store_true", help="跳过回填阶段")
    parser.add_argument("--skip-summary", action="store_true", help="跳过摘要阶段")
    parser.add_argument("--skip-score", action="store_true", help="跳过相关度打分")
    parser.add_argument("--skip-export", action="store_true", help="跳过导出阶段")
    parser.add_argument("--export-report-tag", type=str, default=None, help="导出批次标签 (例如 2025-09-20-AM)")
    parser.add_argument("--export-skip-exported", action=argparse.BooleanOptionalAction, default=True, help="导出时是否跳过历史已导出的文章")
    parser.add_argument("--export-record-history", action=argparse.BooleanOptionalAction, default=True, help="导出后是否记录历史")
    parser.add_argument("--skip-cleanup", action="store_true", help="跳过 AuthorFetch 清理")
    parser.add_argument("--backend", choices=[BACKEND_AUTO, BACKEND_SQLITE, BACKEND_SUPABASE], default=BACKEND_AUTO, help="数据存储后端 (默认 auto)")
    return parser.parse_args()


def build_import_command(backend: str, python_exec: str, import_src: Path, db_path: Path) -> List[str]:
    if backend == BACKEND_SUPABASE:
        return [python_exec, str(TOOLS_DIR / "import_authorfetch_supabase.py"), "--src", str(import_src)]
    return [
        python_exec,
        str(TOOLS_DIR / "import_authorfetch_to_sqlite.py"),
        "--src",
        str(import_src),
        "--db",
        str(db_path),
    ]


def build_fill_command(
    backend: str,
    python_exec: str,
    db_path: Path,
    fill_limit: int,
    fill_delay: float,
    fill_timeout: int,
) -> List[str]:
    limit_args = []
    if fill_limit and fill_limit > 0:
        limit_args.extend(["--limit", str(fill_limit)])
    if backend == BACKEND_SUPABASE:
        return [
            python_exec,
            str(TOOLS_DIR / "fill_missing_content_supabase.py"),
            *limit_args,
            "--delay",
            str(fill_delay),
            "--timeout",
            str(fill_timeout),
        ]
    return [
        python_exec,
        str(TOOLS_DIR / "fill_missing_content.py"),
        "--db",
        str(db_path),
        *limit_args,
        "--delay",
        str(fill_delay),
        "--timeout",
        str(fill_timeout),
    ]


def build_summary_command(
    backend: str,
    python_exec: str,
    db_path: Path,
    keywords_path: Path,
    summarize_limit: int,
    summarize_concurrency: int,
) -> List[str]:
    limit_args = []
    if summarize_limit and summarize_limit > 0:
        limit_args.extend(["--limit", str(summarize_limit)])
    concurrency_args = []
    if summarize_concurrency and summarize_concurrency > 0:
        concurrency_args.extend(["--concurrency", str(summarize_concurrency)])
    if backend == BACKEND_SUPABASE:
        return [
            python_exec,
            str(TOOLS_DIR / "summarize_supabase.py"),
            "--keywords",
            str(keywords_path),
            *limit_args,
            *concurrency_args,
        ]
    return [
        python_exec,
        str(TOOLS_DIR / "summarize_news.py"),
        "--db",
        str(db_path),
        "--keywords",
        str(keywords_path),
        *limit_args,
        *concurrency_args,
    ]


def build_score_command(
    backend: str,
    python_exec: str,
    db_path: Path,
    score_concurrency: int,
) -> List[str]:
    concurrency_args = []
    if score_concurrency and score_concurrency > 0:
        concurrency_args.extend(["--concurrency", str(score_concurrency)])
    if backend == BACKEND_SUPABASE:
        return [python_exec, str(TOOLS_DIR / "score_correlation_supabase.py"), *concurrency_args]
    return [
        python_exec,
        str(TOOLS_DIR / "score_correlation_fulltext.py"),
        "--db",
        str(db_path),
        *concurrency_args,
    ]


def build_export_command(
    backend: str,
    python_exec: str,
    db_path: Path,
    export_output: Path,
    min_score: int,
    export_report_tag: Optional[str],
    skip_exported: bool,
    record_history: bool,
) -> List[str]:
    cmd = []
    if backend == BACKEND_SUPABASE:
        cmd = [
            python_exec,
            str(TOOLS_DIR / "export_high_correlation_supabase.py"),
            "--output",
            str(export_output),
            "--min-score",
            str(min_score),
        ]
    else:
        cmd = [
            python_exec,
            str(TOOLS_DIR / "export_high_correlation.py"),
            "--db",
            str(db_path),
            "--output",
            str(export_output),
            "--min-score",
            str(min_score),
        ]
    if export_report_tag:
        cmd.extend(["--report-tag", export_report_tag])
    if not skip_exported:
        cmd.append("--no-skip-exported")
    if not record_history:
        cmd.append("--no-record-history")
    return cmd


def build_cleanup_command(
    backend: str,
    python_exec: str,
    db_path: Path,
    import_src: Path,
    apply: bool,
    allow_empty: bool,
) -> List[str]:
    if backend == BACKEND_SUPABASE:
        cmd = [python_exec, str(TOOLS_DIR / "cleanup_authorfetch_supabase.py"), "--src", str(import_src)]
        if apply:
            cmd.append("--apply")
        if allow_empty:
            cmd.append("--allow-empty-content")
        return cmd
    cmd = [
        python_exec,
        str(TOOLS_DIR / "cleanup_authorfetch_outputs.py"),
        "--src",
        str(import_src),
        "--db",
        str(db_path),
    ]
    if apply:
        cmd.append("--apply")
    if allow_empty:
        cmd.append("--allow-empty-content")
    return cmd


def main() -> None:
    args = parse_args()
    backend = detect_backend(args.backend)
    python_exec = sys.executable

    db_path = args.db.resolve()
    keywords_path = args.keywords.resolve()
    export_output = args.export_output.resolve()
    import_src = args.import_src.resolve()

    if backend == BACKEND_SUPABASE:
        print("[info] Using Supabase backend")
    else:
        print("[info] Using SQLite backend")

    if not args.skip_import:
        run_step(
            "Import AuthorFetch outputs",
            build_import_command(backend, python_exec, import_src, db_path),
        )

    if not args.skip_fill:
        run_step(
            "Backfill missing content",
            build_fill_command(backend, python_exec, db_path, args.fill_limit, args.fill_delay, args.fill_timeout),
        )

    if not args.skip_summary:
        run_step(
            "Summarize filtered articles",
            build_summary_command(
                backend,
                python_exec,
                db_path,
                keywords_path,
                args.summarize_limit,
                args.summarize_concurrency,
            ),
        )

    if not args.skip_score:
        run_step(
            "Score summary correlation",
            build_score_command(backend, python_exec, db_path, args.score_concurrency),
        )

    export_report_tag = args.export_report_tag
    if not args.skip_export:
        if export_report_tag is None:
            today_tag = datetime.now().strftime("%Y-%m-%d")
            default_tag = f"{today_tag}-ZB"
            print(f"导出报告标签默认值为 {default_tag}")
            prompt = "直接回车使用默认值，输入后缀（例如 ZM）或全量标签以改写: "
            user_input = input(prompt).strip()
            if user_input:
                export_report_tag = user_input if '-' in user_input else f"{today_tag}-{user_input}"
            else:
                export_report_tag = default_tag
        run_step(
            "Export high correlation summaries",
            build_export_command(
                backend,
                python_exec,
                db_path,
                export_output,
                args.min_score,
                export_report_tag,
                args.export_skip_exported,
                args.export_record_history,
            ),
        )

    if not args.skip_cleanup:
        run_step(
            "Cleanup imported AuthorFetch outputs",
            build_cleanup_command(
                backend,
                python_exec,
                db_path,
                import_src,
                args.cleanup_apply,
                args.allow_empty_content,
            ),
        )

    print("\nPipeline completed.")


if __name__ == "__main__":
    main()
