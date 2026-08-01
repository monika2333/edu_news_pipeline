from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.llm_beijing_gate import (
    BeijingGateIndeterminateError,
    call_beijing_gate,
)
from src.config import get_settings
from src.domain import (
    BeijingGateCandidate,
    determine_candidate_category,
    is_beijing_related,
    load_beijing_keywords,
)
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "geo_classify"


@dataclass(slots=True)
class LocalRoutingStats:
    routed_external: int = 0
    staged_gate: int = 0
    failed: int = 0
    skipped: int = 0


def _determine_route(
    article: dict[str, Any],
    beijing_keywords: list[str],
) -> tuple[Optional[bool], str]:
    beijing_related: Optional[bool] = None
    if beijing_keywords:
        detection_payload = [
            str(article.get("llm_summary") or "").strip(),
            str(article.get("title") or "").strip(),
            str(article.get("content_markdown") or "").strip(),
        ]
        beijing_related = is_beijing_related(detection_payload, beijing_keywords)

    sentiment = str(article.get("sentiment_label") or "").strip().lower()
    if sentiment not in {"positive", "negative"}:
        raise ValueError(f"Unsupported sentiment label: {sentiment or '<empty>'}")
    if beijing_related is True:
        return True, "pending_beijing_gate"
    return beijing_related, "pending_external_filter"


def _route_locally(
    adapter: Any,
    rows: list[dict[str, Any]],
    beijing_keywords: list[str],
) -> LocalRoutingStats:
    stats = LocalRoutingStats()
    for article in rows:
        article_id = str(article.get("article_id") or "").strip()
        if not article_id:
            stats.skipped += 1
            continue
        try:
            beijing_related, status = _determine_route(article, beijing_keywords)
            adapter.news_summaries.complete_routing(
                article_id,
                beijing_related=beijing_related,
                status=status,
            )
            if status == "pending_beijing_gate":
                stats.staged_gate += 1
            else:
                stats.routed_external += 1
            log_info(WORKER, f"LOCAL {article_id} beijing={beijing_related} -> {status}")
        except Exception as exc:
            stats.failed += 1
            log_error(WORKER, article_id, exc)
    return stats


def _beijing_gate_raw_payload(
    result: Mapping[str, Any],
    raw_text: str,
) -> dict[str, Any]:
    payload = {
        "model_output": raw_text,
        "parsed_is_beijing_related": result.get("is_beijing_related"),
        "parsed_reason": result.get("reason"),
    }
    for field in ("provider", "model", "attempts"):
        value = result.get(field)
        if value is not None:
            payload[field] = value
    return payload


def _beijing_gate_failure_payload(
    exc: Exception,
    *,
    fail_count: int,
    fallback: Optional[str] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": str(exc),
        "fail_count": fail_count,
    }
    if isinstance(exc, BeijingGateIndeterminateError):
        payload.update(exc.diagnostic_payload())
    if fallback:
        payload["fallback"] = fallback
    return payload


def _process_beijing_gate(
    adapter: Any,
    candidates: list[BeijingGateCandidate],
    executor: ThreadPoolExecutor,
    *,
    llm_retries: int,
    max_failures: int,
) -> tuple[int, int, int, int]:
    confirmed = 0
    rerouted = 0
    failures = 0
    fallback_completed = 0
    future_map = {
        executor.submit(call_beijing_gate, candidate, retries=llm_retries): candidate
        for candidate in candidates
    }

    for future in as_completed(future_map):
        candidate = future_map[future]
        try:
            decision = future.result()
            raw_payload = _beijing_gate_raw_payload(
                {
                    "is_beijing_related": decision.is_beijing_related,
                    "reason": decision.reason,
                    "provider": getattr(decision, "provider", None),
                    "model": getattr(decision, "model", None),
                    "attempts": getattr(decision, "attempts", None),
                },
                decision.raw_text,
            )
            if decision.is_beijing_related is True:
                category = determine_candidate_category(True, candidate.sentiment_label)
                adapter.process.complete_beijing_gate(
                    candidate.article_id,
                    status="ready_for_export",
                    is_beijing_related=True,
                    is_beijing_related_llm=True,
                    raw_output=raw_payload,
                    external_importance_status="ready_for_export",
                    reset_external_filter=False,
                    sentiment_label=candidate.sentiment_label,
                    candidate_category=category,
                )
                confirmed += 1
                log_info(WORKER, f"GATE {candidate.article_id}: confirmed Beijing")
            elif decision.is_beijing_related is False:
                category = determine_candidate_category(False, candidate.sentiment_label)
                adapter.process.complete_beijing_gate(
                    candidate.article_id,
                    status="pending_external_filter",
                    is_beijing_related=False,
                    is_beijing_related_llm=False,
                    raw_output=raw_payload,
                    external_importance_status="pending_external_filter",
                    reset_external_filter=True,
                    sentiment_label=candidate.sentiment_label,
                    candidate_category=category,
                )
                rerouted += 1
                log_info(WORKER, f"GATE {candidate.article_id}: classified outside Beijing")
            else:
                raise RuntimeError("Beijing gate returned indeterminate result")
        except Exception as exc:
            failures += 1
            new_fail_count = candidate.beijing_gate_fail_count + 1
            if new_fail_count >= max_failures:
                fallback_is_beijing = (
                    candidate.is_beijing_related
                    if candidate.is_beijing_related is not None
                    else True
                )
                category = determine_candidate_category(
                    fallback_is_beijing,
                    candidate.sentiment_label,
                )
                adapter.process.complete_beijing_gate(
                    candidate.article_id,
                    status="ready_for_export",
                    is_beijing_related=fallback_is_beijing,
                    is_beijing_related_llm=None,
                    raw_output=_beijing_gate_failure_payload(
                        exc,
                        fail_count=new_fail_count,
                        fallback="ready_for_export",
                    ),
                    external_importance_status="ready_for_export",
                    reset_external_filter=False,
                    sentiment_label=candidate.sentiment_label,
                    candidate_category=category,
                )
                fallback_completed += 1
                log_error(WORKER, candidate.article_id, exc)
                log_info(
                    WORKER,
                    f"GATE FALLBACK {candidate.article_id}: fail_count={new_fail_count}",
                )
            else:
                adapter.process.mark_beijing_gate_failure(
                    candidate.article_id,
                    fail_count=new_fail_count,
                    error=str(exc),
                    raw_output=_beijing_gate_failure_payload(
                        exc,
                        fail_count=new_fail_count,
                    ),
                )
                log_error(WORKER, candidate.article_id, exc)
    return confirmed, rerouted, failures, fallback_completed


def _process_gate_backlog(
    adapter: Any,
    executor: ThreadPoolExecutor,
    *,
    limit: Optional[int],
    batch_size: int,
    llm_retries: int,
    max_failures: int,
) -> tuple[int, int, int, int]:
    totals = [0, 0, 0, 0]
    remaining = limit
    attempted_article_ids: set[str] = set()
    while remaining is None or remaining > 0:
        fetch_size = batch_size if remaining is None else min(batch_size, remaining)
        fetched_candidates = adapter.process.fetch_beijing_gate_candidates(
            fetch_size,
            max_failures=max_failures,
        )
        candidates = [
            candidate
            for candidate in fetched_candidates
            if candidate.article_id not in attempted_article_ids
        ]
        if not candidates:
            break
        attempted_article_ids.update(candidate.article_id for candidate in candidates)
        result = _process_beijing_gate(
            adapter,
            candidates,
            executor,
            llm_retries=llm_retries,
            max_failures=max_failures,
        )
        for index, value in enumerate(result):
            totals[index] += value
        if remaining is not None:
            remaining -= len(candidates)
    return totals[0], totals[1], totals[2], totals[3]


def run(limit: int = 500, *, concurrency: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()
    limit_value = limit if limit and limit > 0 else None
    if settings.process_limit is not None:
        limit_value = (
            settings.process_limit
            if limit_value is None
            else min(limit_value, settings.process_limit)
        )
    workers = concurrency or settings.default_concurrency or 5
    workers = max(1, workers)
    batch_size = max(1, settings.external_filter_batch_size)
    max_failures = max(1, settings.beijing_gate_max_retries or 1)
    llm_retries = max(1, settings.beijing_gate_max_retries or 1)
    beijing_keywords = load_beijing_keywords(settings.beijing_keywords_path)

    with worker_session(WORKER, limit=limit_value):
        rows = adapter.news_summaries.fetch_pending_routes(limit_value)
        local_stats = _route_locally(adapter, rows, beijing_keywords)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            confirmed, rerouted, gate_failures, fallbacks = _process_gate_backlog(
                adapter,
                executor,
                limit=limit_value,
                batch_size=batch_size,
                llm_retries=llm_retries,
                max_failures=max_failures,
            )

        if not rows and not any((confirmed, rerouted, gate_failures, fallbacks)):
            log_info(WORKER, "No pending geographic classifications found.")
        log_info(
            WORKER,
            "result detail: "
            f"external={local_stats.routed_external} staged_gate={local_stats.staged_gate} "
            f"confirmed={confirmed} rerouted={rerouted} fallbacks={fallbacks}",
        )
        completed = local_stats.routed_external + confirmed + rerouted + fallbacks
        log_summary(
            WORKER,
            ok=completed,
            failed=local_stats.failed + gate_failures,
            skipped=local_stats.skipped or None,
        )


__all__ = ["run"]
