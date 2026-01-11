from __future__ import annotations

from typing import List, Optional, Tuple

from src.adapters.db_postgres_core import get_adapter
from src.config import get_settings
from src.domain import is_beijing_related, load_beijing_keywords
from src.workers import log_info, log_summary, worker_session

WORKER = "geo-tag"
DEFAULT_BATCH_SIZE = 200


def _build_detection_payload(row: dict) -> List[str]:
    payload: List[str] = []
    summary = row.get("llm_summary")
    if summary:
        payload.append(str(summary))
    content = row.get("content_markdown")
    if content:
        payload.append(str(content))
    keywords = row.get("llm_keywords") or []
    for keyword in keywords:
        if keyword:
            payload.append(str(keyword))
    return payload


def run(*, limit: Optional[int] = None, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
    settings = get_settings()
    adapter = get_adapter()
    keywords = load_beijing_keywords(settings.beijing_keywords_path)

    if not keywords:
        log_info(WORKER, "No Beijing keywords configured; skipped.")
        return

    processed = 0
    tagged_true = 0
    tagged_false = 0

    with worker_session(WORKER, limit=limit):
        while True:
            remaining = None if limit is None else max(0, limit - processed)
            if remaining == 0:
                break
            fetch_size = batch_size if limit is None else max(1, min(batch_size, remaining))
            rows = adapter.fetch_beijing_tag_candidates(fetch_size)
            if not rows:
                break

            updates: List[Tuple[str, bool]] = []
            for row in rows:
                article_id = str(row.get("article_id") or "").strip()
                if not article_id:
                    continue
                detection_payload = _build_detection_payload(row)
                is_related = is_beijing_related(detection_payload, keywords)
                updates.append((article_id, is_related))
                if is_related:
                    tagged_true += 1
                else:
                    tagged_false += 1
            if not updates:
                break
            adapter.update_beijing_related_bulk(updates)
            processed += len(updates)

    log_summary(WORKER, ok=processed, failed=0, skipped=None)
    log_info(WORKER, f"updated true={tagged_true}, false={tagged_false}")


__all__ = ["run"]
