from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.external_filter_model import (
    call_external_filter_model,
    parse_external_filter_score,
    prompt_key_for_category,
    prompt_version_for_key,
)
from src.config import get_settings
from src.domain import ExternalFilterCandidate
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "external_filter"


def _should_pass(score: Optional[int], threshold: int) -> bool:
    return score is not None and score >= threshold


def _score_candidate(
    candidate: ExternalFilterCandidate,
    *,
    retries: int,
    thresholds: Mapping[str, int],
) -> tuple[int, str, bool, str, str, str]:
    category = candidate.candidate_category
    prompt_key = prompt_key_for_category(category)
    prompt_version = prompt_version_for_key(prompt_key)
    base_category = (category.split("_", 1)[0] if category else "").strip().lower()
    if base_category not in {"internal", "external"}:
        base_category = "external"
    threshold = (
        thresholds.get(category)
        or thresholds.get(base_category)
        or thresholds.get("external")
        or 0
    )
    raw_output = call_external_filter_model(
        candidate,
        category=category,
        retries=retries,
    )
    score_value = parse_external_filter_score(raw_output)
    if score_value is None:
        raise RuntimeError("Model did not return a numeric score")
    passed = _should_pass(score_value, threshold)
    return score_value, raw_output, passed, category, prompt_key, prompt_version


def _process_external_filter_batch(
    adapter: Any,
    candidates: list[ExternalFilterCandidate],
    executor: ThreadPoolExecutor,
    thresholds: Mapping[str, int],
    max_retries: int,
    remaining_limit: Optional[int],
) -> tuple[int, int, int]:
    processed = 0
    failed = 0
    filter_ready = 0
    future_map = {
        executor.submit(
            _score_candidate,
            candidate,
            retries=max_retries,
            thresholds=thresholds,
        ): candidate
        for candidate in candidates
    }

    for future in as_completed(future_map):
        candidate = future_map[future]
        if remaining_limit is not None and processed >= remaining_limit:
            future.cancel()
            continue
        try:
            score, raw, passed, category, prompt_key, prompt_version = future.result()
            adapter.complete_external_filter(
                candidate.article_id,
                passed=passed,
                score=score,
                raw_output=raw,
                category=category,
                prompt_key=prompt_key,
                prompt_version=prompt_version,
            )
            state = "ready_for_export" if passed else "external_filtered"
            log_info(WORKER, f"{category.upper()} OK {candidate.article_id}: score={score} -> {state}")
            if passed:
                filter_ready += 1
            processed += 1
        except Exception as exc:
            failed += 1
            new_fail_count = candidate.external_filter_fail_count + 1
            adapter.mark_external_filter_failure(
                candidate.article_id,
                fail_count=new_fail_count,
                final_failure=new_fail_count >= max_retries,
                error=str(exc),
            )
            log_error(WORKER, f"{candidate.candidate_category.upper()} {candidate.article_id}", exc)
    return processed, failed, filter_ready


def run(limit: Optional[int] = None, concurrency: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()
    batch_size = settings.external_filter_batch_size
    remaining = None if limit is None else max(limit, 0)
    total_processed = 0
    total_failed = 0
    filter_ready = 0
    max_retries = settings.external_filter_max_retries
    thresholds: Mapping[str, int] = {
        "external": settings.external_filter_threshold,
        "external_positive": settings.external_filter_threshold,
        "external_negative": settings.external_filter_negative_threshold,
        "internal": settings.internal_filter_threshold,
        "internal_positive": settings.internal_filter_threshold,
        "internal_negative": settings.internal_filter_negative_threshold,
    }
    workers = max(1, concurrency or settings.default_concurrency or 5)

    with worker_session(WORKER, limit=limit):
        with ThreadPoolExecutor(max_workers=workers) as executor:
            while True:
                fetch_size = batch_size
                if remaining is not None:
                    if remaining <= 0:
                        break
                    fetch_size = min(fetch_size, remaining)
                candidates = adapter.fetch_external_filter_candidates(
                    fetch_size,
                    max_failures=max_retries,
                )
                if not candidates:
                    if total_processed + total_failed == 0:
                        log_info(WORKER, "No pending importance filter candidates.")
                    break

                processed, failed, ready_count = _process_external_filter_batch(
                    adapter,
                    candidates,
                    executor,
                    thresholds,
                    max_retries,
                    remaining,
                )
                total_processed += processed
                total_failed += failed
                filter_ready += ready_count
                if remaining is not None:
                    remaining -= processed
                if processed == 0 and failed == 0:
                    break

        log_info(WORKER, f"Promoted {filter_ready} articles to ready_for_export")
        log_summary(WORKER, ok=total_processed, failed=total_failed)


__all__ = ["run"]
