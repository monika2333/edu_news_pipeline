from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.adapters.llm_chat import LLMQuotaError
from src.adapters.llm_source import detect_source
from src.adapters.llm_summary import summarise
from src.adapters.sentiment_classifier import classify_sentiment
from src.config import get_settings
from src.domain import is_beijing_related, load_beijing_keywords
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "summarize"
DEFAULT_FETCH_MULTIPLIER = 4
MAX_RETRIES = 3


@dataclass(slots=True)
class SummaryStats:
    success: int = 0
    failed: int = 0
    skipped: int = 0
    failure_ids: list[str] = field(default_factory=list)
    summary_seconds: float = 0.0
    sentiment_seconds: float = 0.0
    source_seconds: float = 0.0


@dataclass(slots=True)
class SummaryResult:
    summary_text: str
    sentiment_label: Optional[str]
    sentiment_confidence: Optional[float]
    llm_source: Optional[str]
    source_error: Optional[str]
    summary_seconds: float
    sentiment_seconds: float
    source_seconds: float

    @property
    def total_seconds(self) -> float:
        return self.summary_seconds + self.sentiment_seconds + self.source_seconds


PendingTask = tuple[Future[SummaryResult], dict[str, Any], str, int]


def _normalize_keywords(value: Optional[Sequence[str]]) -> list[str]:
    if not value:
        return []
    result: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _content_from_row(article: dict[str, Any]) -> str:
    return str(article.get('content_markdown') or '').strip()


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _run_llm_pipeline(article: dict[str, Any]) -> SummaryResult:
    content = _content_from_row(article)
    summary_started = time.perf_counter()
    summary_payload = {'title': article.get('title'), 'content': content}
    summary_payload_result = summarise(summary_payload)
    summary_seconds = time.perf_counter() - summary_started
    summary_text = str(summary_payload_result.get('summary') or '').strip()
    if not summary_text:
        raise RuntimeError('Summarisation returned empty text')

    sentiment_started = time.perf_counter()
    sentiment_payload = classify_sentiment(summary_text)
    sentiment_seconds = time.perf_counter() - sentiment_started
    sentiment_label = str(sentiment_payload.get("label") or "").strip() or None
    sentiment_confidence = _optional_float(sentiment_payload.get("confidence"))

    llm_source = None
    source_error = None
    source_started = time.perf_counter()
    try:
        source_result = detect_source(
            {'title': article.get('title'), 'content_markdown': content, 'content': content}
        )
        raw_llm_source = source_result.get('llm_source')
        if isinstance(raw_llm_source, str) and raw_llm_source.strip():
            llm_source = raw_llm_source.strip()
    except LLMQuotaError:
        raise
    except Exception as exc:
        source_error = str(exc)
    source_seconds = time.perf_counter() - source_started

    return SummaryResult(
        summary_text=summary_text,
        sentiment_label=sentiment_label,
        sentiment_confidence=sentiment_confidence,
        llm_source=llm_source,
        source_error=source_error,
        summary_seconds=summary_seconds,
        sentiment_seconds=sentiment_seconds,
        source_seconds=source_seconds,
    )


def _submit_article(
    article: dict[str, Any],
    executor: ThreadPoolExecutor,
    adapter: Any,
    stats: SummaryStats,
) -> Optional[PendingTask]:
    article_id = str(article.get('article_id') or '').strip()
    if not article_id:
        stats.skipped += 1
        return None
    content = _content_from_row(article)
    if not content:
        stats.skipped += 1
        adapter.mark_summary_failed(article_id, message='empty content')
        return None
    previous_failures = int(article.get('summary_fail_count') or 0)
    attempt_count = previous_failures + 1
    if not adapter.mark_summary_attempt(article_id):
        stats.skipped += 1
        return None
    future = executor.submit(_run_llm_pipeline, article)
    return future, article, article_id, attempt_count


def _determine_route(
    result: SummaryResult,
    article: dict[str, Any],
    beijing_keywords: list[str],
) -> tuple[Optional[bool], str, str]:
    beijing_related: Optional[bool] = None
    if beijing_keywords:
        detection_payload = [
            result.summary_text,
            str(article.get("title") or "").strip(),
            _content_from_row(article),
        ]
        beijing_related = is_beijing_related(detection_payload, beijing_keywords)

    sentiment_value = (result.sentiment_label or "").lower()
    if beijing_related is True:
        return beijing_related, "pending_beijing_gate", "pending_beijing_gate"
    if sentiment_value in {"positive", "negative"}:
        return beijing_related, "pending_external_filter", "pending_external_filter"
    return beijing_related, "ready_for_export", "ready_for_export"


def _complete_summary(
    adapter: Any,
    article_id: str,
    result: SummaryResult,
    keywords: list[str],
    beijing_related: Optional[bool],
    next_status: str,
    external_importance_status: str,
) -> None:
    adapter.complete_summary(
        article_id,
        result.summary_text,
        llm_source=result.llm_source,
        keywords=keywords,
        beijing_related=beijing_related,
        sentiment_label=result.sentiment_label,
        sentiment_confidence=result.sentiment_confidence,
        status=next_status,
        external_importance_status=external_importance_status,
        external_importance_score=None,
        external_importance_checked_at=None,
        external_importance_raw=None,
        external_filter_attempted_at=None,
        external_filter_fail_count=0,
        is_beijing_related_llm=None,
        beijing_gate_checked_at=None,
        beijing_gate_raw=None,
        beijing_gate_attempted_at=None,
        beijing_gate_fail_count=0,
    )


def _process_result(
    entry: PendingTask,
    adapter: Any,
    beijing_keywords: list[str],
    stats: SummaryStats,
) -> None:
    future, article, article_id, attempt_count = entry
    try:
        result = future.result()
        if result.source_error:
            log_info(WORKER, f'Source detection skipped {article_id}: {result.source_error}')

        keywords = _normalize_keywords(article.get('llm_keywords'))
        beijing_related, next_status, external_importance_status = _determine_route(
            result, article, beijing_keywords
        )
        _complete_summary(
            adapter,
            article_id,
            result,
            keywords,
            beijing_related,
            next_status,
            external_importance_status,
        )

        stats.success += 1
        stats.summary_seconds += result.summary_seconds
        stats.sentiment_seconds += result.sentiment_seconds
        stats.source_seconds += result.source_seconds
        sentiment = (
            f" sentiment={result.sentiment_label} ({result.sentiment_confidence})"
            if result.sentiment_label
            else ""
        )
        log_info(
            WORKER,
            f"OK {article_id}{sentiment} timing="
            f"summary:{result.summary_seconds:.2f}s sentiment:{result.sentiment_seconds:.2f}s "
            f"source:{result.source_seconds:.2f}s total:{result.total_seconds:.2f}s",
        )
    except Exception as exc:
        stats.failed += 1
        stats.failure_ids.append(article_id)
        log_error(WORKER, article_id, exc)
        if attempt_count >= MAX_RETRIES:
            adapter.mark_summary_failed(article_id, message=str(exc))


def run(limit: int = 500, *, concurrency: Optional[int] = None, keywords_path: Optional[Path] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()
    beijing_keywords = load_beijing_keywords(settings.beijing_keywords_path)

    limit_value: Optional[int]
    if limit and limit > 0:
        limit_value = limit
    else:
        limit_value = None

    process_cap = settings.process_limit
    if process_cap is not None:
        if limit_value is None:
            limit_value = process_cap
        else:
            limit_value = min(limit_value, process_cap)

    max_workers = concurrency
    if max_workers is None:
        max_workers = settings.summary_concurrency or settings.default_concurrency or 5
    max_workers = max(1, max_workers)

    fetch_target = limit_value or max_workers
    fetch_limit = max(1, fetch_target) * DEFAULT_FETCH_MULTIPLIER
    session_limit = limit_value or fetch_target

    # keywords_path is no longer used in the two-stage flow but kept for CLI compatibility
    _ = keywords_path

    with worker_session(WORKER, limit=session_limit):
        rows = adapter.fetch_pending_summaries(fetch_limit, max_attempts=MAX_RETRIES)
        if not rows:
            log_info(WORKER, 'No pending summaries found.')
            log_summary(WORKER, ok=0, failed=0, skipped=None)
            return

        stats = SummaryStats()
        pending_tasks: dict[Future[SummaryResult], tuple[dict[str, Any], str, int]] = {}
        row_iterator = iter(rows)
        rows_exhausted = False

        log_info(WORKER, f"Using {max_workers} workers for end-to-end LLM processing")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while pending_tasks or not rows_exhausted:
                target_workers = max_workers
                if limit_value is not None:
                    target_workers = min(target_workers, limit_value - stats.success)

                while not rows_exhausted and len(pending_tasks) < target_workers:
                    try:
                        article = next(row_iterator)
                    except StopIteration:
                        rows_exhausted = True
                        break
                    entry = _submit_article(article, executor, adapter, stats)
                    if entry is not None:
                        future, pending_article, article_id, attempt_count = entry
                        pending_tasks[future] = (pending_article, article_id, attempt_count)

                if not pending_tasks:
                    if rows_exhausted or target_workers <= 0:
                        break
                    continue

                completed, _ = wait(pending_tasks, return_when=FIRST_COMPLETED)
                for future in completed:
                    pending_article, article_id, attempt_count = pending_tasks.pop(future)
                    _process_result(
                        (future, pending_article, article_id, attempt_count),
                        adapter,
                        beijing_keywords,
                        stats,
                    )

                if limit_value is not None and stats.success >= limit_value:
                    break

        if stats.success:
            log_info(
                WORKER,
                f"LLM timing averages: summary={stats.summary_seconds / stats.success:.2f}s "
                f"sentiment={stats.sentiment_seconds / stats.success:.2f}s "
                f"source={stats.source_seconds / stats.success:.2f}s",
            )

        log_summary(
            WORKER,
            ok=stats.success,
            failed=stats.failed or None,
            skipped=stats.skipped or None,
        )
        if stats.failure_ids:
            log_info(WORKER, f"failed ids: {', '.join(stats.failure_ids)}")


__all__ = ["run"]
