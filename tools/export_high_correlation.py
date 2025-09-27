"""Export high-correlation news summaries to a text file, with export history tracking."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "articles.sqlite3"
DEFAULT_OUTPUT_BASENAME = "high_correlation_summaries.txt"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "outputs" / DEFAULT_OUTPUT_BASENAME
DEFAULT_THRESHOLD = 60
EXPORT_HISTORY_TABLE = "export_history"

CATEGORY_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    (
        "市委教委",
        (
            "市委教委",
            "市委教育工委",
            "市教委",
            "市教育委员会",
            "教育两委",
            "市委教育局",
            "市教育两委",
            "首都教育两委",
        ),
    ),
    (
        "中小学",
        (
            "中小学",
            "小学",
            "初中",
            "高中",
            "义务教育",
            "基础教育",
            "中学",
            "幼儿园",
            "幼儿",
            "托育",
            "K12",
            "教研员",
            "班主任",
            "中职",
        ),
    ),
    (
        "高校",
        (
            "高校",
            "大学",
            "学院",
            "本科",
            "研究生",
            "硕士",
            "博士",
            "博士生",
            "校园",
            "高校教师",
            "高校学生",
        ),
    ),
)

DEFAULT_CATEGORY = "其他社会新闻"



CATEGORY_ORDER: Sequence[str] = tuple(rule[0] for rule in CATEGORY_RULES) + (DEFAULT_CATEGORY,)


def ensure_history_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS export_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_id TEXT NOT NULL,
            report_tag TEXT NOT NULL DEFAULT '',
            output_path TEXT,
            exported_at TEXT DEFAULT (datetime('now','localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_export_history_article_tag
        ON export_history(article_id, report_tag)
        """
    )
    conn.commit()


def load_already_exported(conn: sqlite3.Connection) -> Set[str]:
    rows = conn.execute(
        f"SELECT article_id FROM {EXPORT_HISTORY_TABLE}"
    ).fetchall()
    return {str(row[0]) for row in rows}


def write_history(
    conn: sqlite3.Connection,
    article_ids: Sequence[str],
    *,
    report_tag: str,
    output_path: Path,
) -> None:
    if not article_ids:
        return
    payload: Iterable[Tuple[str, str, str]] = (
        (aid, report_tag, str(output_path)) for aid in article_ids
    )
    conn.executemany(
        f"INSERT OR IGNORE INTO {EXPORT_HISTORY_TABLE}(article_id, report_tag, output_path) VALUES(?,?,?)",
        payload,
    )
    conn.commit()


def classify_category(*text_fields: str | None) -> str:
    """Return the category label based on configured keyword heuristics."""
    haystack = " ".join(part for part in text_fields if part)
    haystack_lower = haystack.lower()
    if not haystack_lower:
        return DEFAULT_CATEGORY
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword.lower() in haystack_lower:
                return category
    return DEFAULT_CATEGORY


def export_high_correlation(
    db_path: Path,
    output_path: Path,
    *,
    min_score: int = DEFAULT_THRESHOLD,
    dry_run: bool = False,
    report_tag: str | None = None,
    skip_exported: bool = True,
    record_history: bool = True,
) -> Tuple[int, int]:
    """Select news summaries with correlation >= min_score and write them out.

    Returns a tuple of (exported_count, skipped_as_history_count).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    ensure_history_table(conn)

    already_exported: Set[str] = set()
    if skip_exported:
        already_exported = load_already_exported(conn)

    rows = conn.execute(
        """
        SELECT article_id, title, summary, source, source_LLM, content, correlation
        FROM news_summaries
        WHERE correlation >= ?
        ORDER BY correlation DESC, id ASC
        """,
        (min_score,),
    ).fetchall()

    grouped_entries: Dict[str, List[str]] = {category: [] for category in CATEGORY_ORDER}
    grouped_ids: Dict[str, List[str]] = {category: [] for category in CATEGORY_ORDER}
    skipped_due_history = 0
    for row in rows:
        article_id = str(row["article_id"])
        if skip_exported and article_id in already_exported:
            skipped_due_history += 1
            continue
        title = (row["title"] or "").strip()
        summary = (row["summary"] or "").strip()
        source = (row["source"] or "").strip()
        source_llm = (row["source_LLM"] or "").strip()
        content = (row["content"] or "").strip()
        suffix = f"（{source_llm}）" if source_llm else ""
        entry = f"{title}\n{summary}{suffix}"
        category = classify_category(source, source_llm, title, summary, content)
        grouped_entries.setdefault(category, []).append(entry)
        grouped_ids.setdefault(category, []).append(article_id)

    category_counts = {category: len(grouped_entries.get(category, [])) for category in CATEGORY_ORDER}

    entries: List[str] = []
    exported_ids: List[str] = []
    for category in CATEGORY_ORDER:
        entries.extend(grouped_entries.get(category, []))
        exported_ids.extend(grouped_ids.get(category, []))

    category_summary = "; ".join(f"{category}:{count}" for category, count in category_counts.items())

    if dry_run:
        print(
            f"Dry run: would export {len(entries)} summaries to {output_path} "
            f"(skipped {skipped_due_history} already exported; category counts: {category_summary})"
        )
        conn.close()
        return len(entries), skipped_due_history

    final_output = output_path
    if report_tag:
        safe_tag = report_tag.replace("/", "_").replace("\\", "_")
        if '-' in safe_tag:
            parts = safe_tag.split('-')
            if len(parts) >= 2:
                date_part = ''.join(parts[:-1])
                suffix_part = parts[-1]
                safe_tag = f"{date_part}_{suffix_part}"
            else:
                safe_tag = safe_tag.replace('-', '_')
        safe_tag = safe_tag.replace(' ', '_')
        base = output_path.stem
        suffix = output_path.suffix or ''
        final_output = output_path.parent / f"{base}_{safe_tag}{suffix}"

    final_output.parent.mkdir(parents=True, exist_ok=True)
    final_output.write_text("\n\n".join(entries), encoding="utf-8")
    print(
        f"Exported {len(entries)} summaries to {final_output} "
        f"(skipped {skipped_due_history} already exported; category counts: {category_summary})"
    )

    if record_history and exported_ids:
        tag = report_tag or ""
        write_history(conn, exported_ids, report_tag=tag, output_path=final_output)

    conn.close()
    return len(entries), skipped_due_history




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export high correlation summaries")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="news_summaries 所在的 SQLite 路径")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="导出文本文件路径")
    parser.add_argument("--min-score", type=int, default=DEFAULT_THRESHOLD, help="过滤使用的最低相关度分")
    parser.add_argument("--dry-run", action="store_true", help="只统计数量不写出文件")
    parser.add_argument("--report-tag", type=str, default=None, help="导出报告标签 (如 2025-09-20-AM)")
    parser.add_argument(
        "--skip-exported",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否跳过历史已导出的文章 (默认启用)",
    )
    parser.add_argument(
        "--record-history",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否记录到 export_history 表 (默认记录)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export_high_correlation(
        args.db.resolve(),
        args.output.resolve(),
        min_score=args.min_score,
        dry_run=args.dry_run,
        report_tag=args.report_tag,
        skip_exported=args.skip_exported,
        record_history=args.record_history,
    )


if __name__ == "__main__":
    main()
