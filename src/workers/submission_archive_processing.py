from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.domain.submission_archive_config import LINK_WINDOW_DAYS
from src.domain.submission_archive_linker import (
    LinkCandidate,
    LinkCandidateSelection,
    build_link_candidate_index,
    score_link_candidate_selection,
    select_link_candidates,
)
from src.workers import log_info, worker_session

WORKER = "submission_archive_processing"
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def launch_submission_report_processing(report_id: str) -> int:
    command = [
        sys.executable,
        "-m",
        "src.workers.submission_archive_processing",
        report_id,
    ]
    process_kwargs: dict[str, Any] = {
        "cwd": _REPO_ROOT,
    }
    if sys.platform == "win32":
        process_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        process_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **process_kwargs)
    return process.pid


def _link_report(report: Mapping[str, Any]) -> dict[str, int]:
    adapter = get_adapter()
    processing_items = [
        item
        for item in report.get("items") or []
        if item.get("link_status") == "processing"
    ]
    counts = {
        "exact": 0,
        "fuzzy": 0,
        "pending": 0,
        "unmatched": 0,
    }
    if not processing_items:
        return counts

    title_candidates = [
        LinkCandidate(
            article_id=str(row["article_id"]),
            title=str(row.get("title") or ""),
        )
        for row in adapter.submission_archive.fetch_link_candidate_titles(
            compiled_date=report["compiled_date"],
            window_days=LINK_WINDOW_DAYS,
        )
    ]
    candidate_index = build_link_candidate_index(title_candidates)
    selections: list[
        tuple[Mapping[str, Any], LinkCandidateSelection]
    ] = []
    required_article_ids: dict[str, None] = {}
    for item in processing_items:
        selection = select_link_candidates(
            str(item.get("title") or ""),
            candidate_index,
        )
        selections.append((item, selection))
        for article_id in selection.required_article_ids():
            required_article_ids.setdefault(article_id, None)

    body_rows = adapter.submission_archive.fetch_link_candidate_bodies(
        article_ids=list(required_article_ids),
    )
    candidate_bodies = {
        str(row["article_id"]): str(row.get("body") or "")
        for row in body_rows
    }
    results: list[dict[str, Any]] = []
    for item, selection in selections:
        linked = score_link_candidate_selection(
            str(item.get("title") or ""),
            str(item.get("body") or ""),
            selection,
            candidate_bodies,
        )
        counts[linked.status] += 1
        results.append(
            {
                "item_id": str(item["id"]),
                **asdict(linked),
            }
        )
    adapter.submission_archive.update_link_results(results)
    return counts


def process_report_links(report_id: str) -> dict[str, int]:
    adapter = get_adapter()
    report = adapter.submission_archive.fetch_report(report_id)
    if not report:
        return {
            "exact": 0,
            "fuzzy": 0,
            "pending": 0,
            "unmatched": 0,
        }
    return _link_report(report)


def process_submission_report(report_id: str) -> dict[str, int]:
    with worker_session(WORKER):
        link_summary = process_report_links(report_id)
        log_info(WORKER, f"Linked report {report_id}: {link_summary}")
    return link_summary


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Process submission archive links outside the console server",
    )
    parser.add_argument("report_id")
    args = parser.parse_args(argv)
    process_submission_report(str(args.report_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "launch_submission_report_processing",
    "main",
    "process_report_links",
    "process_submission_report",
]
