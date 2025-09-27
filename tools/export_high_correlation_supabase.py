#!/usr/bin/env python3
"""Export Supabase summaries with high relevance."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

try:
    from tools.supabase_adapter import ExportCandidate, get_supabase_adapter
except ModuleNotFoundError:
    sys.path.append(str(Path(__file__).resolve().parent))
    from supabase_adapter import ExportCandidate, get_supabase_adapter  # type: ignore

CATEGORY_RULES: Sequence[Tuple[str, Tuple[str, ...]]] = (
    (
        "市委教委",
        (
            "市委教委",
            "市委教育工委",
            "市教委",
            "首都教育两委",
            "教育两委",
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
            "幼儿园",
            "幼儿",
            "托育",
            "K12",
            "班主任",
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
        ),
    ),
)
DEFAULT_CATEGORY = "其他社会新闻"
CATEGORY_ORDER = tuple(rule[0] for rule in CATEGORY_RULES) + (DEFAULT_CATEGORY,)

SECTION_MAP: Dict[str, str] = {
    "市委教委": "other",
    "中小学": "primary_school",
    "高校": "higher_education",
    DEFAULT_CATEGORY: "other",
}


@dataclass
class ExportOptions:
    min_score: int
    report_tag: str
    skip_exported: bool
    record_history: bool
    output_path: Path


def classify_category(*text_fields: str | None) -> str:
    haystack = " ".join(filter(None, text_fields))
    lower = haystack.lower()
    if not lower:
        return DEFAULT_CATEGORY
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword.lower() in lower:
                return category
    return DEFAULT_CATEGORY


def generate_output_path(base_path: Path, report_tag: str) -> Path:
    if not report_tag:
        return base_path
    safe_tag = report_tag.replace("/", "_").replace("\\", "_").replace(" ", "_")
    if '-' in safe_tag:
        parts = safe_tag.split('-')
        if len(parts) >= 4:
            date_part = ''.join(parts[:3])
            suffix = '-'.join(parts[3:])
            safe_tag = f"{date_part}_{suffix}"
        else:
            safe_tag = '_'.join(parts)
    base = base_path.stem
    suffix = base_path.suffix or ""
    return base_path.parent / f"{base}_{safe_tag}{suffix}"


def export_supabase(options: ExportOptions) -> None:
    adapter = get_supabase_adapter()
    candidates = adapter.fetch_export_candidates(options.min_score)
    if not candidates:
        print("No Supabase summaries meet the score threshold.")
        return

    existing_ids, _ = adapter.get_export_history(options.report_tag) if options.skip_exported else (set(), None)

    grouped_entries: Dict[str, List[str]] = {category: [] for category in CATEGORY_ORDER}
    grouped_candidates: Dict[str, List[ExportCandidate]] = {category: [] for category in CATEGORY_ORDER}
    skipped_history = 0

    for candidate in candidates:
        if options.skip_exported and candidate.filtered_article_id in existing_ids:
            skipped_history += 1
            continue
        category = classify_category(candidate.source, candidate.source_llm, candidate.title, candidate.summary, candidate.content)
        grouped_entries.setdefault(category, []).append(
            f"{candidate.title or ''}\n{candidate.summary}{f'（{candidate.source_llm}）' if candidate.source_llm else ''}"
        )
        grouped_candidates.setdefault(category, []).append(candidate)

    category_counts = {category: len(grouped_entries.get(category, [])) for category in CATEGORY_ORDER}

    entries: List[str] = []
    export_payload: List[Tuple[ExportCandidate, str]] = []
    for category in CATEGORY_ORDER:
        items = grouped_entries.get(category, [])
        cand_items = grouped_candidates.get(category, [])
        if not items:
            continue
        section = SECTION_MAP.get(category, "other")
        entries.extend(items)
        export_payload.extend((cand, section) for cand in cand_items)

    final_output = generate_output_path(options.output_path, options.report_tag)
    final_output.parent.mkdir(parents=True, exist_ok=True)
    final_output.write_text("\n\n".join(entries), encoding="utf-8")

    if options.record_history and export_payload:
        adapter.record_export(options.report_tag, export_payload, output_path=str(final_output))

    category_summary = "; ".join(f"{category}:{count}" for category, count in category_counts.items())
    print(
        f"Exported {len(entries)} summaries to {final_output} "
        f"(skipped {skipped_history} already exported; category counts: {category_summary})"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Supabase high-correlation summaries")
    parser.add_argument("--min-score", type=int, default=60, help="Minimum relevance score")
    parser.add_argument("--report-tag", type=str, default=None, help="Report tag (e.g., 2025-09-27-ZB)")
    parser.add_argument("--skip-exported", action=argparse.BooleanOptionalAction, default=True, help="Skip items already exported")
    parser.add_argument("--record-history", action=argparse.BooleanOptionalAction, default=True, help="Record export history")
    parser.add_argument("--output", type=Path, default=Path("outputs/high_correlation_summaries.txt"), help="Output file path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.report_tag is None:
        from datetime import datetime

        today_tag = datetime.now().strftime("%Y-%m-%d-ZB")
        args.report_tag = today_tag
    options = ExportOptions(
        min_score=args.min_score,
        report_tag=args.report_tag,
        skip_exported=args.skip_exported,
        record_history=args.record_history,
        output_path=args.output.resolve(),
    )
    export_supabase(options)


if __name__ == "__main__":
    main()
