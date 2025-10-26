from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.adapters.db import get_adapter
from src.adapters.external_filter_model import (
    call_external_filter_model,
    parse_external_filter_score,
)
from src.config import get_settings
from src.domain import ExternalFilterCandidate
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "external_filter"


def _should_pass(score: Optional[int], threshold: int) -> bool:
    return score is not None and score >= threshold


def run(limit: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()
    batch_size = settings.external_filter_batch_size
    remaining = None if limit is None else max(limit, 0)
    total_processed = 0
    total_failed = 0

    with worker_session(WORKER, limit=limit):
        while True:
            fetch_size = batch_size
            if remaining is not None:
                if remaining <= 0:
                    break
                fetch_size = min(fetch_size, remaining)
            candidates = adapter.fetch_external_filter_candidates(
                fetch_size,
                max_failures=settings.external_filter_max_retries,
            )
            if not candidates:
                if total_processed + total_failed == 0:
                    log_info(WORKER, "No pending external filter candidates.")
                break

            max_retries = settings.external_filter_max_retries
            threshold = settings.external_filter_threshold

            processed = 0
            failed = 0
            for candidate in candidates:
                if remaining is not None and processed >= remaining:
                    break
                try:
                    raw_output = call_external_filter_model(candidate, retries=max_retries)
                    score_value = parse_external_filter_score(raw_output)
                    passed = _should_pass(score_value, threshold)
                    if score_value is None:
                        raise RuntimeError("Model did not return a numeric score")
                    adapter.complete_external_filter(
                        candidate.article_id,
                        passed=passed,
                        score=score_value,
                        raw_output=raw_output,
                    )
                    state = "ready_for_export" if passed else "external_filtered"
                    log_info(
                        WORKER,
                        f"OK {candidate.article_id}: score={score_value} -> {state}",
                    )
                    processed += 1
                except Exception as exc:
                    failed += 1
                    new_fail_count = candidate.external_filter_fail_count + 1
                    final_failure = new_fail_count >= max_retries
                    adapter.mark_external_filter_failure(
                        candidate.article_id,
                        fail_count=new_fail_count,
                        final_failure=final_failure,
                        error=str(exc),
                    )
                    log_error(WORKER, candidate.article_id, exc)

            total_processed += processed
            total_failed += failed
            if remaining is not None:
                remaining -= processed

            if processed == 0 and failed == 0:
                break

        skipped = None
        log_summary(WORKER, ok=total_processed, failed=total_failed or None, skipped=skipped)


__all__ = ["run"]
