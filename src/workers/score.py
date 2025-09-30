from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from src.adapters.db_supabase import get_adapter
from src.adapters.llm_scoring import score_text
from src.config import get_settings
from src.domain import SummaryForScoring


def _score_item(item: SummaryForScoring) -> Optional[int]:
    text = item.content or item.summary
    if not text.strip():
        return None
    return score_text(text)


def run(limit: int = 100, *, concurrency: Optional[int] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()

    rows = adapter.fetch_summaries_for_scoring(limit)
    if not rows:
        print("No summaries pending relevance scoring.")
        return

    workers = concurrency or settings.default_concurrency
    workers = max(1, workers)

    success = 0
    failed = 0

    if workers == 1:
        for row in rows:
            try:
                value = _score_item(row)
                adapter.update_relevance_score(row.article_id, value)
                success += 1
                print(f"OK {row.article_id}: {value}")
            except Exception as exc:
                failed += 1
                print(f"FAIL {row.article_id}: {exc}")
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            future_map = {pool.submit(_score_item, row): row.article_id for row in rows}
            for future in as_completed(future_map):
                article_id = future_map[future]
                try:
                    value = future.result()
                    adapter.update_relevance_score(article_id, value)
                    success += 1
                    print(f"OK {article_id}: {value}")
                except Exception as exc:
                    failed += 1
                    print(f"FAIL {article_id}: {exc}")

    print(f"done. ok={success} failed={failed} total={len(rows)}")


__all__ = ["run"]

