#!/usr/bin/env python3
"""Export Supabase summaries with high relevance."""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

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


def fetch_candidates(min_score: int):
    adapter = get_supabase_adapter()
    resp = (
        adapter.client
        .table("news_summaries")
        .select("article_id, title, llm_summary, correlation, content_markdown, source, url, publish_time_iso, llm_keywords")
        .gte("correlation", min_score)
        .order("correlation", desc=True)
    ).execute()
    items = resp.data or []
    return items


def export_supabase(options: ExportOptions) -> None:
    adapter = get_supabase_adapter()
    rows = fetch_candidates(options.min_score)
    if not rows:
        print("No Supabase summaries meet the score threshold.")
        return

    if options.skip_exported:
        existing_ids, _ = adapter.get_export_history(options.report_tag)
        global_exported_ids: Set[str] = adapter.get_all_exported_article_ids()
    else:
        existing_ids = set()
        global_exported_ids = set()
    skip_ids = global_exported_ids

    grouped_entries: Dict[str, List[str]] = {category: [] for category in CATEGORY_ORDER}
    grouped_candidates: Dict[str, List[Dict[str, any]]] = {category: [] for category in CATEGORY_ORDER}
    skipped_current_tag = 0
    skipped_previous_reports = 0

    for row in rows:
        article_id = str(row.get("article_id"))
        if options.skip_exported and article_id in skip_ids:
            if article_id in existing_ids:
                skipped_current_tag += 1
            else:
                skipped_previous_reports += 1
            continue
        category = classify_category(row.get("source"), None, row.get("title"), row.get("llm_summary"), row.get("content_markdown"))
        source_suffix = row.get('source')
        if source_suffix:
            entry = f"{row.get('title') or ''}\n{row.get('llm_summary') or ''}（{source_suffix}）"
        else:
            entry = f"{row.get('title') or ''}\n{row.get('llm_summary') or ''}"
        grouped_entries.setdefault(category, []).append(entry)
        grouped_candidates.setdefault(category, []).append(row)

    category_counts = {category: len(grouped_entries.get(category, [])) for category in CATEGORY_ORDER}

    entries: List[str] = []
    export_payload: List[Tuple[Dict[str, any], str]] = []
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
        payload = []
        for cand, section in export_payload:
            payload.append(
                ExportCandidate(
                    filtered_article_id=str(cand.get("article_id")),
                    raw_article_id=str(cand.get("article_id")),
                    article_hash=str(cand.get("article_id")),
                    title=cand.get("title"),
                    summary=cand.get("llm_summary") or "",
                    content=str(cand.get("content_markdown") or ""),
                    source=cand.get("source"),
                    source_llm=None,
                    relevance_score=float(cand.get("correlation") or 0),
                    original_url=cand.get("url"),
                    published_at=cand.get("publish_time_iso"),
                )
            )
        adapter.record_export(options.report_tag, list(zip(payload, [section for _, section in export_payload])), output_path=str(final_output))

    category_summary = "; ".join(f"{category}:{count}" for category, count in category_counts.items())
    total_skipped = skipped_current_tag + skipped_previous_reports
    skip_detail = ""
    if total_skipped:
        detail_parts = []
        if skipped_current_tag:
            detail_parts.append(f"this_tag:{skipped_current_tag}")
        if skipped_previous_reports:
            detail_parts.append(f"previous_tags:{skipped_previous_reports}")
        if detail_parts:
            skip_detail = f" [{', '.join(detail_parts)}]"
    print(
        f"Exported {len(entries)} summaries to {final_output} "
        f"(skipped {total_skipped} already exported{skip_detail}; category counts: {category_summary})"
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
