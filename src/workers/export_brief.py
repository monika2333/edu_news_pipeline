from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from src.adapters.db_supabase import get_adapter
from src.domain import ExportCandidate
from src.workers import log_info, log_summary, worker_session

WORKER = "export"

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


def classify_category(*parts: Optional[str]) -> str:
    haystack = " ".join(filter(None, parts)).lower()
    if not haystack:
        return DEFAULT_CATEGORY
    for category, keywords in CATEGORY_RULES:
        for keyword in keywords:
            if keyword.lower() in haystack:
                return category
    return DEFAULT_CATEGORY


def generate_report_tag(date_str: Optional[str], report_tag: Optional[str]) -> str:
    if report_tag:
        return report_tag
    if date_str:
        return date_str
    return datetime.now().strftime("%Y-%m-%d")


def generate_output_path(base_path: Path, report_tag: str) -> Path:
    safe_tag = report_tag.replace("/", "_").replace("\\", "_").replace(" ", "_")
    if "-" in safe_tag:
        parts = safe_tag.split("-")
        if len(parts) >= 4:
            date_part = "".join(parts[:3])
            suffix = "-".join(parts[3:])
            safe_tag = f"{date_part}_{suffix}"
        else:
            safe_tag = "_".join(parts)
    base = base_path.stem
    suffix = base_path.suffix or ""
    return base_path.parent / f"{base}_{safe_tag}{suffix}"


def run(
    limit: Optional[int] = None,
    *,
    date: Optional[str] = None,
    min_score: int = 70,
    report_tag: Optional[str] = None,
    skip_exported: bool = True,
    record_history: bool = True,
    output_base: Optional[Path] = None,
) -> None:
    adapter = get_adapter()

    with worker_session(WORKER, limit=limit if limit is not None else min_score):
        tag = generate_report_tag(date, report_tag)
        base_output = output_base or Path("outputs") / "high_correlation_summaries.txt"
        if not base_output.is_absolute():
            base_output = (Path.cwd() / base_output).resolve()

        candidates = adapter.fetch_export_candidates(min_score)
        if not candidates:
            log_info(WORKER, "No filtered articles meet the score threshold.")
            return
        if limit is not None:
            candidates = candidates[:max(0, limit)]

        existing_ids: Set[str] = set()
        global_exported: Set[str] = set()
        if skip_exported:
            existing_ids, _ = adapter.get_export_history(tag)
            global_exported = adapter.get_all_exported_article_ids()

        grouped_entries: Dict[str, List[str]] = {category: [] for category in CATEGORY_ORDER}
        grouped_candidates: Dict[str, List[ExportCandidate]] = {category: [] for category in CATEGORY_ORDER}

        skipped_current_tag = 0
        skipped_previous_reports = 0

        for candidate in candidates:
            article_id = candidate.filtered_article_id
            if skip_exported and article_id in global_exported:
                if article_id in existing_ids:
                    skipped_current_tag += 1
                else:
                    skipped_previous_reports += 1
                continue
            category = classify_category(candidate.source, candidate.title, candidate.summary, candidate.content)
            summary_line = candidate.summary or ""
            if candidate.source:
                entry = f"{candidate.title or ''}\n{summary_line}（{candidate.source}）"
            else:
                entry = f"{candidate.title or ''}\n{summary_line}"
            grouped_entries.setdefault(category, []).append(entry)
            grouped_candidates.setdefault(category, []).append(candidate)

        category_counts = {category: len(grouped_entries.get(category, [])) for category in CATEGORY_ORDER}

        export_payload: List[Tuple[ExportCandidate, str]] = []
        text_entries: List[str] = []
        for category in CATEGORY_ORDER:
            items = grouped_entries.get(category, [])
            cand_items = grouped_candidates.get(category, [])
            if not items:
                continue
            section = SECTION_MAP.get(category, "other")
            text_entries.extend(items)
            export_payload.extend((cand, section) for cand in cand_items)

        if not text_entries:
            log_info(WORKER, "No entries to export after filtering/skip logic.")
            return

        final_output = generate_output_path(base_output, tag)
        final_output.parent.mkdir(parents=True, exist_ok=True)
        final_output.write_text("\n\n".join(text_entries), encoding="utf-8")

        if record_history and export_payload:
            adapter.record_export(tag, export_payload, output_path=str(final_output))

        category_summary = "; ".join(f"{category}:{count}" for category, count in category_counts.items())
        total_skipped = skipped_current_tag + skipped_previous_reports
        if category_summary:
            log_info(WORKER, f"category breakdown: {category_summary}")
        if total_skipped:
            detail = []
            if skipped_current_tag:
                detail.append(f"this_tag:{skipped_current_tag}")
            if skipped_previous_reports:
                detail.append(f"previous_tags:{skipped_previous_reports}")
            log_info(WORKER, "skipped " + ", ".join(detail))

        log_info(WORKER, f"output -> {final_output}")
        log_summary(WORKER, ok=len(export_payload), failed=0, skipped=total_skipped if total_skipped else None)


__all__ = ["run"]
