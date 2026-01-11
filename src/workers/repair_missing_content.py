from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.http_toutiao import FeedItem, build_detail_update as tt_build_detail_update, fetch_info as tt_fetch_info
from src.adapters.http_chinanews import (
    FeedItemLike as CNFeedItem,
    fetch_detail as cn_fetch_detail,
    build_detail_update as cn_build_detail_update,
)
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "repair"
DEFAULT_RETRY_LIMIT = 3


def _row_to_toutiao_item(row: Dict[str, Any]) -> FeedItem:
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
            # Route by source prefix: chinanews:* vs toutiao numeric IDs
            if article_id.startswith('chinanews:'):
                url = str(row.get('url') or '').strip()
                if not url:
                    failures += 1
                    log_error(WORKER, f"missing_url:{article_id}", RuntimeError('missing url for chinanews row'))
                    continue
                try:
                    # Minimal CN item from row
                    publish_iso = row.get('publish_time_iso')
                    if isinstance(publish_iso, datetime):
                        publish_iso = publish_iso.isoformat()
                    cn_item = CNFeedItem(
                        title=str(row.get('title') or ''),
                        url=url,
                        section=str(row.get('source') or ''),
                        publish_time_iso=publish_iso,
                        raw={},
                    )
                except Exception as exc:
                    failures += 1
                    log_error(WORKER, f"row_to_cn_item:{article_id}", exc)
                    continue
                attempts = 0
                detail_payload = None
                while attempts < DEFAULT_RETRY_LIMIT:
                    attempts += 1
                    try:
                        detail_payload = cn_fetch_detail(url)
                        break
                    except Exception as exc:
                        if attempts >= DEFAULT_RETRY_LIMIT:
                            failures += 1
                            log_error(WORKER, f"cn_fetch_detail:{article_id}", exc)
                        else:
                            continue
                if detail_payload is None:
                    continue
                try:
                    detail_rows.append(
                        cn_build_detail_update(
                            cn_item,
                            article_id,
                            detail_payload,
                            detail_fetched_at=datetime.now(timezone.utc),
                        )
                    )
                except Exception as exc:
                    failures += 1
                    log_error(WORKER, f"cn_build_update:{article_id}", exc)
            else:
                # Toutiao fallback (numeric IDs)
                try:
                    item = _row_to_toutiao_item(row)
                except Exception as exc:
                    failures += 1
                    log_error(WORKER, f"row_to_feed:{article_id}", exc)
                    continue
                attempts = 0
                detail_payload = None
                while attempts < DEFAULT_RETRY_LIMIT:
                    attempts += 1
                    try:
                        detail_payload = tt_fetch_info(article_id)
                        break
                    except Exception as exc:
                        if attempts >= DEFAULT_RETRY_LIMIT:
                            failures += 1
                            log_error(WORKER, f"fetch_detail:{article_id}", exc)
                        else:
                            continue
                if detail_payload is None:
                    continue
                try:
                    detail_rows.append(
                        tt_build_detail_update(
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

