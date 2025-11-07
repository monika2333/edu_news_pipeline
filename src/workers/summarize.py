from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.adapters.db import get_adapter
from src.adapters.llm_source import detect_source
from src.adapters.llm_summary import summarise
from src.adapters.sentiment_classifier import classify_sentiment
from src.config import get_settings
from src.domain import is_beijing_related, load_beijing_keywords
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "summarize"
DEFAULT_FETCH_MULTIPLIER = 4
MAX_RETRIES = 3


def _normalize_keywords(value: Optional[Sequence[str]]) -> List[str]:
    if not value:
        return []
    result: List[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def _content_from_row(article: Dict[str, Any]) -> str:
    return str(article.get('content_markdown') or '').strip()


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

    max_workers = concurrency or settings.default_concurrency or 5
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

        success = 0
        failed = 0
        skipped = 0
        failure_ids: List[str] = []
        pending_tasks: List[Tuple[Any, Dict[str, Any], str, int]] = []

        def _submit_article(article: Dict[str, Any]) -> None:
            nonlocal skipped
            article_id = str(article.get('article_id') or '').strip()
            if not article_id:
                skipped += 1
                return
            content = _content_from_row(article)
            if not content:
                skipped += 1
                adapter.mark_summary_failed(article_id, message='empty content')
                return
            previous_failures = int(article.get('summary_fail_count') or 0)
            attempt_count = previous_failures + 1
            if not adapter.mark_summary_attempt(article_id):
                skipped += 1
                return
            summary_payload = {
                'title': article.get('title'),
                'content': content,
            }
            future = executor.submit(summarise, summary_payload)
            article['summary_fail_count'] = attempt_count
            pending_tasks.append((future, article, article_id, attempt_count))

        def _process_entry(entry: Tuple[Any, Dict[str, Any], str, int]) -> None:
            nonlocal success, failed
            future, article, article_id, attempt_count = entry
            content = _content_from_row(article)
            try:
                result = future.result()
                summary_text = (result.get('summary', '')).strip()
                if not summary_text:
                    raise RuntimeError('Summarisation returned empty text')
                sentiment_payload = classify_sentiment(summary_text)
                sentiment_label = str(sentiment_payload.get("label") or "").strip() or None
                sentiment_confidence = sentiment_payload.get("confidence")
                llm_source = None
                try:
                    source_payload = {
                        'title': article.get('title'),
                        'content_markdown': content,
                        'content': content,
                    }
                    source_result = detect_source(source_payload)
                    llm_source = (source_result.get('llm_source') or '').strip()
                except Exception as source_exc:
                    log_info(WORKER, f'Source detection skipped {article_id}: {source_exc}')
                keywords = _normalize_keywords(article.get('llm_keywords'))
                beijing_related: Optional[bool] = None
                if beijing_keywords:
                    detection_payload: List[str] = [
                        summary_text,
                        str(article.get("title") or "").strip(),
                        content,
                    ]
                    beijing_related = is_beijing_related(detection_payload, beijing_keywords)
                sentiment_value = (sentiment_label or "").lower()
                sentiment_positive = sentiment_value == "positive"
                sentiment_negative = sentiment_value == "negative"
                if beijing_related is True:
                    next_status = "pending_beijing_gate"
                    external_importance_status = "pending_beijing_gate"
                elif beijing_related is not True and (sentiment_positive or sentiment_negative):
                    next_status = "pending_external_filter"
                    external_importance_status = "pending_external_filter"
                else:
                    next_status = "ready_for_export"
                    external_importance_status = "ready_for_export"

                beijing_gate_defaults = {
                    "is_beijing_related_llm": None,
                    "beijing_gate_checked_at": None,
                    "beijing_gate_raw": None,
                    "beijing_gate_attempted_at": None,
                    "beijing_gate_fail_count": 0,
                }
                adapter.complete_summary(
                    article_id,
                    summary_text,
                    llm_source=llm_source,
                    keywords=keywords,
                    beijing_related=beijing_related,
                    sentiment_label=sentiment_label,
                    sentiment_confidence=sentiment_confidence,
                    status=next_status,
                    external_importance_status=external_importance_status,
                    external_importance_score=None,
                    external_importance_checked_at=None,
                    external_importance_raw=None,
                    external_filter_attempted_at=None,
                    external_filter_fail_count=0,
                    **beijing_gate_defaults,
                )
                success += 1
                if sentiment_label:
                    log_info(WORKER, f'OK {article_id} sentiment={sentiment_label} ({sentiment_confidence})')
                else:
                    log_info(WORKER, f'OK {article_id}')
            except Exception as exc:
                failed += 1
                failure_ids.append(article_id)
                log_error(WORKER, article_id, exc)
                if attempt_count >= MAX_RETRIES:
                    adapter.mark_summary_failed(article_id, message=str(exc))
            finally:
                future = None  # allow GC

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for article in rows:
                if limit_value is not None and success >= limit_value:
                    break
                _submit_article(article)
                while pending_tasks and len(pending_tasks) >= max_workers:
                    entry = pending_tasks.pop(0)
                    _process_entry(entry)
                    if limit_value is not None and success >= limit_value:
                        break
                if limit_value is not None and success >= limit_value:
                    break

            while pending_tasks:
                entry = pending_tasks.pop(0)
                _process_entry(entry)
                if limit_value is not None and success >= limit_value:
                    for future, *_ in pending_tasks:
                        future.cancel()
                    break

        log_summary(
            WORKER,
            ok=success,
            failed=failed or None,
            skipped=skipped or None,
        )
        if failure_ids:
            log_info(WORKER, f"failed ids: {', '.join(failure_ids)}")


__all__ = ["run"]
