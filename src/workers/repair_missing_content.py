from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.adapters.db import get_adapter
from src.adapters.http_toutiao import FeedItem, build_detail_update, fetch_info
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "repair"
DEFAULT_RETRY_LIMIT = 3


def _row_to_feed_item(row: Dict[str, Any]) -> FeedItem:
    publish_time = row.get('publish_time')
    publish_iso = row.get('publish_time_iso')
    if isinstance(publish_iso, datetime):
        publish_iso = publish_iso.isoformat()
    fetched_url = row.get('url') or ''
    return FeedItem(
        token=str(row.get('token') or ''),
        profile_url=str(row.get('profile_url') or ''),
        title=str(row.get('title') or ''),
        summary=str(row.get('summary') or ''),
        source=str(row.get('source') or ''),
        publish_time=publish_time if publish_time is None else int(publish_time),
        publish_time_iso=publish_iso,
        article_url=fetched_url,
        comment_count=int(row.get('comment_count') or 0),
        digg_count=int(row.get('digg_count') or 0),
        raw={"article_id": row.get('article_id')},
    )


def run(limit: Optional[int] = None) -> None:
    adapter = get_adapter()

    with worker_session(WORKER, limit=limit):
        rows = adapter.fetch_raw_articles_missing_content(limit)
        if not rows:
            log_info(WORKER, "No articles with missing content found.")
            log_summary(WORKER, ok=0, failed=0, skipped=None)
            return

        detail_rows: List[Dict[str, Any]] = []
        failures = 0
        for row in rows:
            article_id = str(row.get('article_id') or '')
            if not article_id:
                continue
            try:
                item = _row_to_feed_item(row)
            except Exception as exc:
                failures += 1
                log_error(WORKER, f"row_to_feed:{article_id}", exc)
                continue
            attempts = 0
            while attempts < DEFAULT_RETRY_LIMIT:
                attempts += 1
                try:
                    detail_payload = fetch_info(article_id)
                    break
                except Exception as exc:
                    if attempts >= DEFAULT_RETRY_LIMIT:
                        failures += 1
                        log_error(WORKER, f"fetch_detail:{article_id}", exc)
                    else:
                        continue
            else:
                continue
            try:
                detail_rows.append(
                    build_detail_update(
                        item,
                        article_id,
                        detail_payload,
                        detail_fetched_at=datetime.now(timezone.utc),
                    )
                )
            except Exception as exc:
                failures += 1
                log_error(WORKER, f"build_update:{article_id}", exc)

        success = 0
        if detail_rows:
            try:
                adapter.update_raw_article_details(detail_rows)
            except Exception as exc:
                failures += len(detail_rows)
                log_error(WORKER, "postgres_detail", exc)
            else:
                success = len(detail_rows)
                for record in detail_rows:
                    log_info(WORKER, f"DETAIL REPAIRED {record['article_id']}")

        log_summary(WORKER, ok=success, failed=failures or None, skipped=None)


__all__ = ["run"]

