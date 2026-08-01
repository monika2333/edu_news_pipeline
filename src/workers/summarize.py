from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.llm_summary import summarise
from src.config import get_settings
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "summarize"
DEFAULT_FETCH_MULTIPLIER = 4
MAX_RETRIES = 3


@dataclass(slots=True)
class SummaryStats:
    success: int = 0
    failed: int = 0
    skipped: int = 0
    summary_seconds: float = 0.0
    failure_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SummaryResult:
    summary_text: str
    elapsed_seconds: float


PendingTask = tuple[Future[SummaryResult], str, int]


def _content_from_row(article: dict[str, Any]) -> str:
    return str(article.get("content_markdown") or "").strip()


def _generate_summary(article: dict[str, Any]) -> SummaryResult:
    started = time.perf_counter()
    payload = {
        "title": article.get("title"),
        "content": _content_from_row(article),
    }
    result = summarise(payload)
    summary_text = str(result.get("summary") or "").strip()
    if not summary_text:
        raise RuntimeError("Summarisation returned empty text")
    return SummaryResult(
        summary_text=summary_text,
        elapsed_seconds=time.perf_counter() - started,
    )


def _submit_article(
    article: dict[str, Any],
    executor: ThreadPoolExecutor,
    adapter: Any,
    stats: SummaryStats,
) -> Optional[PendingTask]:
    article_id = str(article.get("article_id") or "").strip()
    if not article_id:
        stats.skipped += 1
        return None
    if not _content_from_row(article):
        stats.skipped += 1
        adapter.news_summaries.mark_failed(article_id, message="empty content")
        return None
    attempt_count = int(article.get("summary_fail_count") or 0) + 1
    if not adapter.news_summaries.mark_attempt(article_id):
        stats.skipped += 1
        return None
    return executor.submit(_generate_summary, article), article_id, attempt_count


def _process_result(entry: PendingTask, adapter: Any, stats: SummaryStats) -> None:
    future, article_id, attempt_count = entry
    try:
        result = future.result()
        adapter.news_summaries.complete_generation(article_id, result.summary_text)
        stats.success += 1
        stats.summary_seconds += result.elapsed_seconds
        log_info(WORKER, f"OK {article_id} timing=summary:{result.elapsed_seconds:.2f}s")
    except Exception as exc:
        stats.failed += 1
        stats.failure_ids.append(article_id)
        log_error(WORKER, article_id, exc)
        if attempt_count >= MAX_RETRIES:
            adapter.news_summaries.mark_failed(article_id, message=str(exc))


def _resolve_limit(limit: int, process_limit: Optional[int]) -> Optional[int]:
    limit_value = limit if limit and limit > 0 else None
    if process_limit is None:
        return limit_value
    return process_limit if limit_value is None else min(limit_value, process_limit)


def run(
    limit: int = 500,
    *,
    concurrency: Optional[int] = None,
    keywords_path: Optional[Path] = None,
) -> None:
    settings = get_settings()
    adapter = get_adapter()
    limit_value = _resolve_limit(limit, settings.process_limit)
    max_workers = concurrency or settings.summary_concurrency or settings.default_concurrency or 5
    max_workers = max(1, max_workers)
    fetch_target = limit_value or max_workers
    fetch_limit = max(1, fetch_target) * DEFAULT_FETCH_MULTIPLIER
    session_limit = limit_value or fetch_target

    # Kept only for backward-compatible CLI invocations.
    _ = keywords_path

    with worker_session(WORKER, limit=session_limit):
        rows = adapter.news_summaries.fetch_pending(fetch_limit, max_attempts=MAX_RETRIES)
        if not rows:
            log_info(WORKER, "No pending summaries found.")
            log_summary(WORKER, ok=0, failed=0)
            return

        stats = SummaryStats()
        pending_tasks: dict[Future[SummaryResult], tuple[str, int]] = {}
        row_iterator = iter(rows)
        rows_exhausted = False
        log_info(WORKER, f"Using {max_workers} workers for summary generation only")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while pending_tasks or not rows_exhausted:
                remaining = None if limit_value is None else limit_value - stats.success
                target_workers = max_workers if remaining is None else min(max_workers, remaining)

                while not rows_exhausted and len(pending_tasks) < target_workers:
                    try:
                        article = next(row_iterator)
                    except StopIteration:
                        rows_exhausted = True
                        break
                    entry = _submit_article(article, executor, adapter, stats)
                    if entry is not None:
                        future, article_id, attempt_count = entry
                        pending_tasks[future] = (article_id, attempt_count)

                if not pending_tasks:
                    if rows_exhausted or target_workers <= 0:
                        break
                    continue

                completed, _ = wait(pending_tasks, return_when=FIRST_COMPLETED)
                for future in completed:
                    article_id, attempt_count = pending_tasks.pop(future)
                    _process_result((future, article_id, attempt_count), adapter, stats)

                if limit_value is not None and stats.success >= limit_value:
                    break

        if stats.success:
            average = stats.summary_seconds / stats.success
            log_info(WORKER, f"LLM timing average: summary={average:.2f}s")
        log_summary(
            WORKER,
            ok=stats.success,
            failed=stats.failed,
            skipped=stats.skipped or None,
        )
        if stats.failure_ids:
            log_info(WORKER, f"failed ids: {', '.join(stats.failure_ids)}")


__all__ = ["run"]
