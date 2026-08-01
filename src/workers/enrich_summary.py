from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Literal, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.llm_source import detect_source
from src.adapters.sentiment_classifier import classify_sentiment
from src.config import get_settings
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "enrich_summary"

TaskKind = Literal["sentiment", "source"]


@dataclass(slots=True)
class EnrichmentResult:
    kind: TaskKind
    elapsed_seconds: float
    label: Optional[str] = None
    confidence: Optional[float] = None
    llm_source: Optional[str] = None


@dataclass(slots=True)
class ArticleEnrichment:
    sentiment: Optional[EnrichmentResult] = None
    source: Optional[EnrichmentResult] = None
    failed: bool = False


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _classify_summary(summary_text: str) -> EnrichmentResult:
    started = time.perf_counter()
    payload = classify_sentiment(summary_text)
    label = str(payload.get("label") or "").strip().lower()
    if label not in {"positive", "negative"}:
        raise RuntimeError(f"Unsupported sentiment label: {label or '<empty>'}")
    return EnrichmentResult(
        kind="sentiment",
        label=label,
        confidence=_optional_float(payload.get("confidence")),
        elapsed_seconds=time.perf_counter() - started,
    )


def _detect_article_source(article: dict[str, Any]) -> EnrichmentResult:
    started = time.perf_counter()
    content = str(article.get("content_markdown") or "").strip()
    payload = detect_source(
        {
            "title": article.get("title"),
            "content_markdown": content,
            "content": content,
        }
    )
    raw_source = payload.get("llm_source")
    llm_source = str(raw_source).strip() if raw_source else None
    return EnrichmentResult(
        kind="source",
        llm_source=llm_source,
        elapsed_seconds=time.perf_counter() - started,
    )


def _submit_tasks(
    rows: list[dict[str, Any]],
    executor: ThreadPoolExecutor,
) -> tuple[
    dict[Future[EnrichmentResult], tuple[str, TaskKind]],
    dict[str, ArticleEnrichment],
]:
    futures: dict[Future[EnrichmentResult], tuple[str, TaskKind]] = {}
    article_results: dict[str, ArticleEnrichment] = {}
    for row in rows:
        article_id = str(row.get("article_id") or "").strip()
        if not article_id:
            continue
        article_results[article_id] = ArticleEnrichment()
        sentiment_future = executor.submit(
            _classify_summary,
            str(row.get("llm_summary") or "").strip(),
        )
        source_future = executor.submit(_detect_article_source, row)
        futures[sentiment_future] = (article_id, "sentiment")
        futures[source_future] = (article_id, "source")
    return futures, article_results


def _collect_results(
    futures: dict[Future[EnrichmentResult], tuple[str, TaskKind]],
    article_results: dict[str, ArticleEnrichment],
) -> tuple[int, int, float, float]:
    task_success = 0
    task_failed = 0
    sentiment_seconds = 0.0
    source_seconds = 0.0
    for future in as_completed(futures):
        article_id, kind = futures[future]
        try:
            result = future.result()
            if kind == "sentiment":
                article_results[article_id].sentiment = result
                sentiment_seconds += result.elapsed_seconds
            else:
                article_results[article_id].source = result
                source_seconds += result.elapsed_seconds
            task_success += 1
            log_info(WORKER, f"OK {kind} {article_id} timing={result.elapsed_seconds:.2f}s")
        except Exception as exc:
            article_results[article_id].failed = True
            task_failed += 1
            log_error(WORKER, f"{kind} {article_id}", exc)
    return task_success, task_failed, sentiment_seconds, source_seconds


def _persist_completed(adapter: Any, results: dict[str, ArticleEnrichment]) -> tuple[int, int]:
    completed = 0
    failed = 0
    for article_id, article_result in results.items():
        sentiment = article_result.sentiment
        source = article_result.source
        if article_result.failed or sentiment is None or source is None:
            failed += 1
            continue
        try:
            adapter.news_summaries.complete_enrichment(
                article_id,
                label=sentiment.label or "",
                confidence=sentiment.confidence,
                llm_source=source.llm_source,
            )
            completed += 1
        except Exception as exc:
            failed += 1
            log_error(WORKER, article_id, exc)
    return completed, failed


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
    max_workers = concurrency or settings.summary_concurrency or settings.default_concurrency or 5
    max_workers = max(1, max_workers)

    with worker_session(WORKER, limit=limit_value):
        rows = adapter.news_summaries.fetch_pending_enrichments(limit_value)
        if not rows:
            log_info(WORKER, "No pending summary enrichments found.")
            log_summary(WORKER, ok=0, failed=0)
            return

        log_info(
            WORKER,
            f"Using {max_workers} workers for independent sentiment and source requests",
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures, article_results = _submit_tasks(rows, executor)
            task_stats = _collect_results(futures, article_results)

        completed, failed = _persist_completed(adapter, article_results)
        task_success, task_failed, sentiment_seconds, source_seconds = task_stats
        sentiment_success = sum(result.sentiment is not None for result in article_results.values())
        source_success = sum(result.source is not None for result in article_results.values())
        timing_parts: list[str] = []
        if sentiment_success:
            timing_parts.append(f"sentiment={sentiment_seconds / sentiment_success:.2f}s")
        if source_success:
            timing_parts.append(f"source={source_seconds / source_success:.2f}s")
        if timing_parts:
            log_info(WORKER, "LLM timing averages: " + " ".join(timing_parts))
        log_info(WORKER, f"tasks: ok={task_success} failed={task_failed}")
        log_summary(WORKER, ok=completed, failed=failed)


__all__ = ["run"]
