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
    if limit is not None:
        batch_size = min(batch_size, max(0, limit))
    with worker_session(WORKER, limit=batch_size):
        candidates = adapter.fetch_external_filter_candidates(
            batch_size,
            max_failures=settings.external_filter_max_retries,
        )
        if not candidates:
            log_info(WORKER, "No pending external filter candidates.")
            return
        max_retries = settings.external_filter_max_retries
        threshold = settings.external_filter_threshold

        processed = 0
        failed = 0
        for candidate in candidates:
            if limit is not None and processed >= limit:
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
        skipped = max(0, len(candidates) - processed - failed)
        log_summary(WORKER, ok=processed, failed=failed, skipped=skipped or None)


__all__ = ["run"]
