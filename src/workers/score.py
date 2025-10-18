from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional, Tuple

from src.adapters.db import get_adapter
from src.adapters.llm_scoring import score_text
from src.config import get_settings
from src.domain import PrimaryArticleForScoring
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "score"
SCORE_THRESHOLD = 60


def _score_item(item: PrimaryArticleForScoring) -> Optional[int]:
    text = item.content or ""
    if not text.strip():
        return None
    return score_text(text)


def run(limit: int = 500, *, concurrency: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()

    with worker_session(WORKER, limit=limit):
        rows = adapter.fetch_primary_articles_for_scoring(limit)
        if not rows:
            log_info(WORKER, "No primary articles pending relevance scoring.")
            return

        workers = concurrency or settings.default_concurrency
        workers = max(1, workers)

        successes: List[Tuple[PrimaryArticleForScoring, Optional[int]]] = []
        failures: List[str] = []

        if workers == 1:
            for row in rows:
                try:
                    value = _score_item(row)
                    successes.append((row, value))
                    log_info(WORKER, f"OK {row.article_id}: {value}")
                except Exception as exc:
                    failures.append(row.article_id)
                    log_error(WORKER, row.article_id, exc)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {pool.submit(_score_item, row): row for row in rows}
                for future in as_completed(future_map):
                    item = future_map[future]
                    article_id = item.article_id
                    try:
                        value = future.result()
                        successes.append((item, value))
                        log_info(WORKER, f"OK {article_id}: {value}")
                    except Exception as exc:
                        failures.append(article_id)
                        log_error(WORKER, article_id, exc)

        updates: List[dict] = []
        promotion_payloads: List[dict] = []
        for item, score_value in successes:
            threshold_met = score_value is not None and score_value >= SCORE_THRESHOLD
            status = "scored" if threshold_met else "filtered_out"
            updates.append(
                {
                    "article_id": item.article_id,
                    "score": score_value,
                    "status": status,
                }
            )
            if threshold_met:
                promotion_payloads.append(
                    {
                        "article_id": item.article_id,
                        "title": item.title,
                        "source": item.source,
                        "publish_time": item.publish_time,
                        "publish_time_iso": item.publish_time_iso,
                        "url": item.url,
                        "content_markdown": item.content,
                        "score": score_value,
                        "status": "pending",
                        "keywords": list(item.keywords),
                    }
                )

        if failures:
            updates.extend(
                {
                    "article_id": article_id,
                    "score": None,
                    "status": "failed",
                }
                for article_id in failures
            )

        if updates:
            adapter.update_primary_article_scores(updates)

        if promotion_payloads:
            adapter.upsert_news_summaries_from_primary(promotion_payloads)
            log_info(WORKER, f"Promoted {len(promotion_payloads)} primary articles to news_summaries")

        success_count = len(successes)
        failed_count = len(failures)
        skipped = len(rows) - success_count - failed_count
        log_summary(WORKER, ok=success_count, failed=failed_count, skipped=skipped or None)


__all__ = ["run"]
