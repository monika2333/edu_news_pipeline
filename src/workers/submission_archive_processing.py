from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_submission_archive import PRIOR_MATCH_REPORT_TYPES
from src.domain.submission_archive_config import (
    LINK_WINDOW_DAYS,
    feedback_lookback_days,
    feedback_match_threshold,
)
from src.domain.submission_archive_linker import (
    LinkCandidate,
    LinkCandidateSelection,
    build_link_candidate_index,
    score_link_candidate_selection,
    select_link_candidates,
)
from src.workers import log_info, worker_session
from src.workers.submission_dedup import (
    _validate_archive_vectors,
    backfill_archive_embeddings,
)

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
        "matched": 0,
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
            "matched": 0,
            "pending": 0,
            "unmatched": 0,
        }
    return _link_report(report)


def _build_prior_item_matches(
    items: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    threshold: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    item_vector_rows = [
        row for row in items if row.get("embedding") is not None
    ]
    candidate_vector_rows = [
        row for row in candidates if row.get("embedding") is not None
    ]
    vector_scores: dict[tuple[str, str], float] = {}
    if item_vector_rows and candidate_vector_rows:
        rows = [*item_vector_rows, *candidate_vector_rows]
        matrix = _validate_archive_vectors(rows).astype(np.float32, copy=False)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        normalized = np.divide(
            matrix,
            norms,
            out=np.zeros_like(matrix),
            where=norms > 0,
        )
        item_count = len(item_vector_rows)
        similarities = normalized[:item_count] @ normalized[item_count:].T
        for item_index, item in enumerate(item_vector_rows):
            for candidate_index, candidate in enumerate(candidate_vector_rows):
                vector_scores[(str(item["id"]), str(candidate["id"]))] = float(
                    np.clip(similarities[item_index, candidate_index], -1.0, 1.0)
                )

    matches: list[dict[str, Any]] = []
    counts = {"article": 0, "title_hash": 0, "vector": 0}
    for item in items:
        item_id = str(item["id"])
        item_article_id = item.get("article_id")
        item_title_hash = item.get("norm_title_hash")
        for candidate in candidates:
            prior_item_id = str(candidate["id"])
            candidate_article_id = candidate.get("article_id")
            candidate_title_hash = candidate.get("norm_title_hash")
            match_method: Optional[str] = None
            similarity: Optional[float] = None
            if (
                item_article_id
                and candidate_article_id
                and item_article_id == candidate_article_id
            ):
                match_method = "article"
                similarity = 1.0
            elif (
                item_title_hash
                and candidate_title_hash
                and item_title_hash == candidate_title_hash
            ):
                match_method = "title_hash"
                similarity = 1.0
            else:
                vector_similarity = vector_scores.get((item_id, prior_item_id))
                if vector_similarity is not None and vector_similarity >= threshold:
                    match_method = "vector"
                    similarity = vector_similarity
            if match_method is None or similarity is None:
                continue
            matches.append(
                {
                    "item_id": item_id,
                    "prior_item_id": prior_item_id,
                    "similarity": similarity,
                    "match_method": match_method,
                }
            )
            counts[match_method] += 1
    return matches, counts


def _empty_prior_match_summary() -> dict[str, int]:
    return {
        "embedded": 0,
        "items": 0,
        "candidates": 0,
        "article": 0,
        "title_hash": 0,
        "vector": 0,
        "matches": 0,
    }


def process_report_prior_matches(
    report_id: str,
    *,
    backfill: bool = True,
) -> dict[str, int]:
    adapter = get_adapter()
    report = adapter.submission_archive.fetch_report(report_id)
    if not report or report.get("report_type") not in PRIOR_MATCH_REPORT_TYPES:
        return _empty_prior_match_summary()

    try:
        embedded = backfill_archive_embeddings() if backfill else 0
        refreshed_report = adapter.submission_archive.fetch_report(report_id)
        if not refreshed_report:
            return _empty_prior_match_summary()
        item_ids = [
            str(item["id"])
            for item in refreshed_report.get("items") or []
        ]
        items = adapter.submission_archive.fetch_item_match_inputs(item_ids)
        candidates = (
            adapter.submission_archive.fetch_prior_submission_candidates(
                report_date=refreshed_report["report_date"],
                lookback_days=feedback_lookback_days(),
            )
        )
        if not candidates:
            log_info(
                WORKER,
                f"No prior submission candidates for report {report_id}.",
            )
        if items and not any(
            item.get("embedding") is not None for item in items
        ):
            log_info(WORKER, f"Report {report_id} has no item embeddings.")

        matches, counts = _build_prior_item_matches(
            items,
            candidates,
            threshold=feedback_match_threshold(),
        )
        persisted = adapter.submission_archive.replace_item_duplicate_matches(
            item_ids=item_ids,
            matches=matches,
        )
        log_info(
            WORKER,
            f"Prior matches for report {report_id}: items={len(items)}, "
            f"candidates={len(candidates)}, article={counts['article']}, "
            f"title_hash={counts['title_hash']}, vector={counts['vector']}.",
        )
        return {
            "embedded": embedded,
            "items": len(items),
            "candidates": len(candidates),
            **counts,
            "matches": persisted,
        }
    finally:
        adapter.submission_archive.mark_prior_match_completed(report_id)


def recompute_feedback_prior_matches(
    report_id: Optional[str] = None,
) -> dict[str, int]:
    if report_id is not None:
        summary = process_report_prior_matches(report_id)
        return {"reports": 1, **summary}

    adapter = get_adapter()
    report_ids: list[str] = []
    for report_type in PRIOR_MATCH_REPORT_TYPES:
        report_ids.extend(
            adapter.submission_archive.fetch_report_ids_by_type(report_type)
        )
    embedded = backfill_archive_embeddings()
    total_matches = 0
    for current_report_id in report_ids:
        summary = process_report_prior_matches(
            current_report_id,
            backfill=False,
        )
        total_matches += summary["matches"]
    return {
        "reports": len(report_ids),
        "embedded": embedded,
        "matches": total_matches,
    }


def process_submission_report(report_id: str) -> dict[str, int]:
    with worker_session(WORKER):
        link_summary = process_report_links(report_id)
        log_info(WORKER, f"Linked report {report_id}: {link_summary}")
        process_report_prior_matches(report_id)
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
    "PRIOR_MATCH_REPORT_TYPES",
    "_build_prior_item_matches",
    "launch_submission_report_processing",
    "main",
    "process_report_prior_matches",
    "process_report_links",
    "process_submission_report",
    "recompute_feedback_prior_matches",
]
