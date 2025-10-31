from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Optional, Tuple, Mapping

from src.adapters.db import get_adapter
from src.adapters.external_filter_model import (
    call_external_filter_model,
    parse_external_filter_score,
)
from src.adapters.llm_beijing_gate import call_beijing_gate
from src.config import get_settings
from src.domain import BeijingGateCandidate, ExternalFilterCandidate
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "external_filter"


def _should_pass(score: Optional[int], threshold: int) -> bool:
    return score is not None and score >= threshold


def _score_candidate(
    candidate: ExternalFilterCandidate,
    *,
    retries: int,
    threshold: int,
) -> Tuple[int, str, bool]:
    raw_output = call_external_filter_model(candidate, retries=retries)
    score_value = parse_external_filter_score(raw_output)
    if score_value is None:
        raise RuntimeError("Model did not return a numeric score")
    passed = _should_pass(score_value, threshold)
    return score_value, raw_output, passed


def _beijing_gate_raw_payload(result: Mapping[str, Any], raw_text: str) -> dict[str, Any]:
    return {
        "model_output": raw_text,
        "parsed_is_beijing_related": result.get("is_beijing_related"),
        "parsed_reason": result.get("reason"),
    }


def _process_beijing_gate(
    adapter,
    candidates: list[BeijingGateCandidate],
    executor,
    *,
    llm_retries: int,
    max_failures: int,
) -> Tuple[int, int, int]:
    confirmed = 0
    rerouted = 0
    failures = 0

    future_map = {
        executor.submit(call_beijing_gate, candidate, retries=llm_retries): candidate
        for candidate in candidates
    }

    for future in as_completed(future_map):
        candidate = future_map[future]
        try:
            decision = future.result()
            decision_raw = {
                "is_beijing_related": decision.is_beijing_related,
                "reason": decision.reason,
            }
            raw_payload = _beijing_gate_raw_payload(decision_raw, decision.raw_text)
            if decision.is_beijing_related is True:
                adapter.complete_beijing_gate(
                    candidate.article_id,
                    status="ready_for_export",
                    is_beijing_related=True,
                    is_beijing_related_llm=True,
                    raw_output=raw_payload,
                    external_importance_status="ready_for_export",
                    reset_external_filter=False,
                    sentiment_label=candidate.sentiment_label,
                    candidate_category="internal",
                )
                confirmed += 1
                log_info(WORKER, f"Gate OK {candidate.article_id}: confirmed Beijing")
            elif decision.is_beijing_related is False:
                adapter.complete_beijing_gate(
                    candidate.article_id,
                    status="pending_external_filter",
                    is_beijing_related=False,
                    is_beijing_related_llm=False,
                    raw_output=raw_payload,
                    external_importance_status="pending_external_filter",
                    reset_external_filter=True,
                    sentiment_label=candidate.sentiment_label,
                    candidate_category="external",
                )
                rerouted += 1
                log_info(WORKER, f"Gate REROUTE {candidate.article_id}: sent to external filter")
            else:
                raise RuntimeError("Beijing gate returned indeterminate result")
        except Exception as exc:
            failures += 1
            new_fail_count = candidate.beijing_gate_fail_count + 1
            if new_fail_count >= max_failures:
                fallback_payload = {
                    "error": str(exc),
                    "fail_count": new_fail_count,
                    "fallback": "ready_for_export",
                }
                adapter.complete_beijing_gate(
                    candidate.article_id,
                    status="ready_for_export",
                    is_beijing_related=candidate.is_beijing_related if candidate.is_beijing_related is not None else True,
                    is_beijing_related_llm=None,
                    raw_output=fallback_payload,
                    external_importance_status="ready_for_export",
                    reset_external_filter=False,
                    sentiment_label=candidate.sentiment_label,
                    candidate_category="internal",
                )
                log_error(WORKER, candidate.article_id, exc)
                log_info(
                    WORKER,
                    f"Gate FALLBACK {candidate.article_id}: fail_count={new_fail_count}, defaulting to ready_for_export",
                )
            else:
                adapter.mark_beijing_gate_failure(
                    candidate.article_id,
                    fail_count=new_fail_count,
                    error=str(exc),
                )
                log_error(WORKER, candidate.article_id, exc)
    return confirmed, rerouted, failures


def run(limit: Optional[int] = None, concurrency: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()
    batch_size = settings.external_filter_batch_size
    remaining = None if limit is None else max(limit, 0)
    total_processed = 0
    total_failed = 0
    max_retries = settings.external_filter_max_retries
    threshold = settings.external_filter_threshold
    workers = concurrency or settings.default_concurrency or 5
    workers = max(1, workers)
    beijing_gate_max_failures = max(1, settings.beijing_gate_max_retries or 1)
    beijing_gate_llm_retries = max(1, settings.beijing_gate_max_retries or 1)

    gate_confirmed = 0
    gate_rerouted = 0
    gate_failures = 0

    with worker_session(WORKER, limit=limit):
        with ThreadPoolExecutor(max_workers=workers) as executor:
            while True:
                fetch_size = batch_size
                if remaining is not None:
                    if remaining <= 0:
                        break
                    fetch_size = min(fetch_size, remaining)
                beijing_candidates = adapter.fetch_beijing_gate_candidates(
                    fetch_size,
                    max_failures=beijing_gate_max_failures,
                )
                if beijing_candidates:
                    confirmed, rerouted, failures = _process_beijing_gate(
                        adapter,
                        beijing_candidates,
                        executor,
                        llm_retries=beijing_gate_llm_retries,
                        max_failures=beijing_gate_max_failures,
                    )
                    gate_confirmed += confirmed
                    gate_rerouted += rerouted
                    gate_failures += failures
                    continue
                candidates = adapter.fetch_external_filter_candidates(
                    fetch_size,
                    max_failures=max_retries,
                )
                if not candidates:
                    if total_processed + total_failed == 0:
                        log_info(WORKER, "No pending external filter candidates.")
                    break

                future_map = {
                    executor.submit(
                        _score_candidate,
                        candidate,
                        retries=max_retries,
                        threshold=threshold,
                    ): candidate
                    for candidate in candidates
                }

                processed = 0
                failed = 0
                for future in as_completed(future_map):
                    candidate = future_map[future]
                    if remaining is not None and processed >= remaining:
                        future.cancel()
                        continue
                    try:
                        score_value, raw_output, passed = future.result()
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

        if gate_confirmed or gate_rerouted or gate_failures:
            log_info(
                WORKER,
                f"Beijing gate summary: confirmed={gate_confirmed}, rerouted={gate_rerouted}, failures={gate_failures}",
            )

        log_summary(
            WORKER,
            ok=total_processed,
            failed=total_failed or None,
            skipped=None,
        )


__all__ = ["run"]
