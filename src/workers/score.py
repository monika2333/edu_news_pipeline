from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from src.adapters.db_supabase import get_adapter
from src.adapters.llm_scoring import score_text
from src.config import get_settings
from src.domain import SummaryForScoring
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "score"


def _score_item(item: SummaryForScoring) -> Optional[int]:
    text = item.content or item.summary
    if not text.strip():
        return None
    return score_text(text)


def run(limit: int = 100, *, concurrency: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()

    with worker_session(WORKER, limit=limit):
        rows = adapter.fetch_summaries_for_scoring(limit)
        if not rows:
            log_info(WORKER, "No summaries pending relevance scoring.")
            return

        workers = concurrency or settings.default_concurrency
        workers = max(1, workers)

        success = 0
        failed = 0

        if workers == 1:
            for row in rows:
                try:
                    value = _score_item(row)
                    adapter.update_correlation(row.article_id, value)
                    success += 1
                    log_info(WORKER, f"OK {row.article_id}: {value}")
                except Exception as exc:
                    failed += 1
                    log_error(WORKER, row.article_id, exc)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                future_map = {pool.submit(_score_item, row): row.article_id for row in rows}
                for future in as_completed(future_map):
                    article_id = future_map[future]
                    try:
                        value = future.result()
                        adapter.update_correlation(article_id, value)
                        success += 1
                        log_info(WORKER, f"OK {article_id}: {value}")
                    except Exception as exc:
                        failed += 1
                        log_error(WORKER, article_id, exc)

        skipped = len(rows) - success - failed
        log_summary(WORKER, ok=success, failed=failed, skipped=skipped if skipped else None)


__all__ = ["run"]
