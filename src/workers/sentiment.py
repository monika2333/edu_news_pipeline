from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from src.adapters.db_postgres import get_adapter
from src.adapters.sentiment_classifier import classify_sentiment
from src.config import get_settings
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "sentiment"


def _collect_updates(
    articles: Sequence[Dict[str, object]],
    *,
    threshold: float,
) -> Tuple[List[Tuple[str, str, float]], int, int, int]:
    settings = get_settings()
    updates: List[Tuple[str, str, float]] = []
    ok = 0
    failed = 0
    skipped = 0
    for article in articles:
        article_id = str(article.get("article_id") or "").strip()
        if not article_id:
            skipped += 1
            continue
        content = str(article.get("content_markdown") or "").strip()
        if not content:
            skipped += 1
            continue
        try:
            label, confidence, source = classify_sentiment(
                content,
                settings=settings,
                threshold=threshold,
            )
            updates.append((article_id, label, confidence))
            ok += 1
            log_info(WORKER, f"{article_id}: {label} ({confidence:.2f}) via {source}")
        except Exception as exc:
            failed += 1
            log_error(WORKER, article_id or "<unknown>", exc)
    return updates, ok, failed, skipped


def run(
    *,
    limit: Optional[int] = None,
    include_low_confidence: bool = False,
) -> Dict[str, int]:
    settings = get_settings()
    adapter = get_adapter()

    fetch_limit = limit if limit and limit > 0 else None
    if fetch_limit is None and settings.process_limit:
        fetch_limit = settings.process_limit

    threshold = settings.sentiment_confidence_threshold

    with worker_session(WORKER, limit=fetch_limit):
        rows = adapter.fetch_filtered_articles_for_sentiment(
            limit=fetch_limit,
            threshold=threshold,
            include_low_confidence=include_low_confidence,
        )
        if not rows:
            log_info(WORKER, "No articles require sentiment classification.")
            log_summary(WORKER, ok=0, failed=0, skipped=None)
            return {"processed": 0, "updated": 0}

        updates, ok, failed, skipped = _collect_updates(rows, threshold=threshold)

        if updates:
            adapter.update_filtered_sentiment_results(
                [(article_id, label, confidence) for article_id, label, confidence in updates]
            )

        log_summary(WORKER, ok=ok, failed=failed or None, skipped=skipped or None)
        return {"processed": len(rows), "updated": len(updates), "failed": failed, "skipped": skipped}


__all__ = ["run"]
