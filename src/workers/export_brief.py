from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

from src.adapters.db import get_adapter
from src.domain import ExportCandidate
from src.notifications.feishu import FeishuConfigError, FeishuRequestError, notify_export_summary
from src.workers import log_info, log_summary, worker_session

WORKER = "export"


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


def _ensure_unique_output(path: Path) -> Path:
    """Return a path with numeric suffixes when the target already exists."""
    if not path.exists():
        return path
    parent = path.parent
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem}({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def run(
    limit: Optional[int] = None,
    *,
    date: Optional[str] = None,
    min_score: int = 60,
    report_tag: Optional[str] = None,
    skip_exported: bool = True,
    record_history: bool = True,
    output_base: Optional[Path] = None,
) -> Optional[Path]:
    adapter = get_adapter()

    with worker_session(WORKER, limit=limit if limit is not None else min_score):
        tag = generate_report_tag(date, report_tag)
        base_output = output_base or Path("outputs") / "high_correlation_summaries.txt"
        if not base_output.is_absolute():
            base_output = (Path.cwd() / base_output).resolve()

        # Fetch candidates already sorted by correlation DESC
        candidates = adapter.fetch_export_candidates(min_score)
        if not candidates:
            log_info(WORKER, "No filtered articles meet the score threshold.")
            return

        existing_ids: Set[str] = set()
        global_exported: Set[str] = set()
        if skip_exported:
            existing_ids, _ = adapter.get_export_history(tag)
            global_exported = adapter.get_all_exported_article_ids()

        skipped_current_tag = 0
        skipped_previous_reports = 0

        # Filter out exported items first, then enforce the limit
        selected_candidates: List[ExportCandidate] = []
        for cand in candidates:
            aid = cand.filtered_article_id
            if skip_exported and aid in global_exported:
                if aid in existing_ids:
                    skipped_current_tag += 1
                else:
                    skipped_previous_reports += 1
                continue
            selected_candidates.append(cand)
            if limit is not None and len(selected_candidates) >= max(0, limit):
                break

        if not selected_candidates:
            log_info(WORKER, "No entries to export after filtering/skip logic.")
            return

        internal_candidates: List[ExportCandidate] = []
        external_candidates: List[ExportCandidate] = []
        for cand in selected_candidates:
            if cand.is_beijing_related is True:
                internal_candidates.append(cand)
            else:
                external_candidates.append(cand)

        def _format_entry(candidate: ExportCandidate) -> str:
            title_line = (candidate.title or "").strip()
            summary_line = (candidate.summary or "").strip()
            display_source = (candidate.llm_source or candidate.source or "").strip()
            if display_source:
                return f"{title_line}\n{summary_line}（{display_source}）"
            return f"{title_line}\n{summary_line}"

        text_entries: List[str] = []
        export_payload: List[Tuple[ExportCandidate, str]] = []

        section_definitions: List[Tuple[str, List[ExportCandidate], str]] = [
            ("京内", internal_candidates, "jingnei"),
            ("京外", external_candidates, "jingwai"),
        ]

        for label, items, section_key in section_definitions:
            if not items:
                continue
            block_lines = [f"【{label}】共 {len(items)} 条"]
            for item in items:
                block_lines.append(_format_entry(item))
                export_payload.append((item, section_key))
            text_entries.append("\n".join(block_lines))

        final_output = generate_output_path(base_output, tag)
        final_output = _ensure_unique_output(final_output)
        final_output.parent.mkdir(parents=True, exist_ok=True)
        final_output.write_text("\n\n".join(text_entries), encoding="utf-8")

        if record_history and export_payload:
            adapter.record_export(tag, export_payload, output_path=str(final_output))

        total_skipped = skipped_current_tag + skipped_previous_reports
        if total_skipped:
            detail = []
            if skipped_current_tag:
                detail.append(f"this_tag:{skipped_current_tag}")
            if skipped_previous_reports:
                detail.append(f"previous_tags:{skipped_previous_reports}")
            log_info(WORKER, "skipped " + ", ".join(detail))

        log_info(WORKER, f"output -> {final_output}")

        try:
            notify_export_summary(
                tag=tag,
                output_path=final_output,
                entries=text_entries,
                category_counts={
                    "京内": len(internal_candidates),
                    "京外": len(external_candidates),
                },
            )
            log_info(WORKER, "Feishu notification sent")
        except FeishuConfigError:
            log_info(WORKER, "Feishu notification skipped (missing credentials)")
        except FeishuRequestError as exc:
            log_info(WORKER, f"Feishu notification failed: {exc}")
        except Exception as exc:  # pragma: no cover - defensive logging
            log_info(WORKER, f"Feishu notification unexpected error: {exc}")
        log_summary(WORKER, ok=len(export_payload), failed=0, skipped=total_skipped if total_skipped else None)
        return final_output


__all__ = ["run"]
