from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.adapters.http_toutiao import (
    FeedItem,
    build_detail_update,
    fetch_feed_items,
    fetch_info,
    feed_item_to_row,
    load_author_tokens,
    resolve_article_id_from_feed,
)
from src.adapters.db import get_adapter
from src.config import get_settings
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "crawl"
DEFAULT_AUTHORS_FILE = Path("data/author_tokens.txt")
DEFAULT_LANG = "zh-CN,zh;q=0.9"
DEFAULT_TIMEOUT = 15


def _truthy_env(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_authors_path() -> Path:
    env_value = os.getenv("TOUTIAO_AUTHORS_PATH")
    if env_value:
        candidate = Path(env_value).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate
    default_path = DEFAULT_AUTHORS_FILE
    if not default_path.is_absolute():
        return _repo_root() / default_path
    return default_path



def _load_author_entries(path: Path) -> List[Tuple[str, str]]:
    return load_author_tokens(path)


def _collect_feed(
    entries: Sequence[Tuple[str, str]],
    limit: Optional[int],
    *,
    show_browser: bool,
    existing_ids: Optional[Set[str]],
):
    return asyncio.run(fetch_feed_items(list(entries), limit, show_browser, existing_ids))


def _prepare_feed_rows(feed_items):
    rows: List[Dict[str, Any]] = []
    items_by_id: Dict[str, FeedItem] = {}
    unresolved = 0
    duplicates = 0
    for item in feed_items:
        try:
            article_id = resolve_article_id_from_feed(item)
        except Exception as exc:
            log_error(WORKER, "feed_article_id", exc)
            unresolved += 1
            continue
        if article_id in items_by_id:
            duplicates += 1
            continue
        rows.append(feed_item_to_row(item, article_id, fetched_at=datetime.now(timezone.utc)))
        items_by_id[article_id] = item
    return rows, items_by_id, unresolved, duplicates



def run(limit: int = 500, *, concurrency: Optional[int] = None) -> None:  # pylint: disable=unused-argument
    settings = get_settings()

    authors_path = _resolve_authors_path()
    show_browser = _truthy_env(os.getenv("TOUTIAO_SHOW_BROWSER"))
    timeout_env = os.getenv("TOUTIAO_FETCH_TIMEOUT")
    try:
        timeout_value = int(timeout_env) if timeout_env is not None else DEFAULT_TIMEOUT
    except ValueError:
        timeout_value = DEFAULT_TIMEOUT
    lang = os.getenv("TOUTIAO_LANG", DEFAULT_LANG)

    process_cap = settings.process_limit
    effective_limit: Optional[int]
    if limit <= 0:
        effective_limit = None
    else:
        effective_limit = limit
    if process_cap is not None:
        if effective_limit is None:
            effective_limit = process_cap
        else:
            effective_limit = min(effective_limit, process_cap)

    adapter = get_adapter()

    with worker_session(WORKER, limit=effective_limit):
        if not authors_path.exists():
            log_info(WORKER, f"Author token file not found: {authors_path}")
            return

        try:
            entries = _load_author_entries(authors_path)
        except Exception as exc:
            log_error(WORKER, authors_path.as_posix(), exc)
            return

        if not entries:
            log_info(WORKER, "Author token list is empty.")
            return

        try:
            existing_ids = adapter.get_existing_toutiao_article_ids()
        except Exception as exc:
            log_error(WORKER, "local_existing", exc)
            existing_ids = set()

        feed_limit = effective_limit
        try:
            feed_items = _collect_feed(entries, feed_limit, show_browser=show_browser, existing_ids=existing_ids)
        except Exception as exc:
            log_error(WORKER, "feed", exc)
            return

        if not feed_items:
            log_info(WORKER, "No feed items returned from Toutiao.")
            return

        feed_rows, feed_index, unresolved_count, duplicate_count = _prepare_feed_rows(feed_items)
        if not feed_rows:
            log_info(WORKER, "No feed rows to upsert after filtering.")
            skipped_total = duplicate_count
            log_summary(WORKER, ok=0, failed=unresolved_count, skipped=skipped_total or None)
            return

        try:
            feed_upserted = adapter.upsert_toutiao_feed_rows(feed_rows)
        except Exception as exc:
            log_error(WORKER, "postgres_feed", exc)
            log_summary(WORKER, ok=0, failed=len(feed_rows), skipped=None)
            return

        log_info(WORKER, f"feed rows upserted: {feed_upserted}")
        if duplicate_count:
            log_info(WORKER, f"duplicate feed items skipped: {duplicate_count}")
        if unresolved_count:
            log_info(WORKER, f"feed items missing article_id: {unresolved_count}")

        missing_content_ids = adapter.get_toutiao_articles_missing_content(list(feed_index.keys()))
        detail_targets = [(article_id, feed_index[article_id]) for article_id in feed_index if article_id in missing_content_ids]
        already_complete = len(feed_index) - len(detail_targets)
        if already_complete:
            log_info(WORKER, f"articles already populated: {already_complete}")
        if detail_targets:
            log_info(WORKER, f"articles needing detail refresh: {len(detail_targets)}")

        detail_rows = []
        detail_fetch_failures = 0
        for article_id, item in detail_targets:
            try:
                detail_payload = fetch_info(article_id, timeout=timeout_value, lang=lang)
            except Exception as exc:
                detail_fetch_failures += 1
                log_error(WORKER, f"detail_fetch:{article_id}", exc)
                continue
            detail_rows.append(
                build_detail_update(
                    item,
                    article_id,
                    detail_payload,
                    detail_fetched_at=datetime.now(timezone.utc),
                )
            )

        detail_db_failures = 0
        if detail_rows:
            try:
                adapter.update_toutiao_article_details(detail_rows)
            except Exception as exc:
                detail_db_failures = len(detail_rows)
                detail_rows = []
                log_error(WORKER, "postgres_detail", exc)
            else:
                for row in detail_rows:
                    log_info(WORKER, f"DETAIL OK {row['article_id']}")

        detail_success_count = len(detail_rows)
        failed_total = detail_fetch_failures + detail_db_failures + unresolved_count
        skipped_total = already_complete + duplicate_count

        if detail_fetch_failures:
            log_info(WORKER, f"detail fetch failures: {detail_fetch_failures}")
        if detail_db_failures:
            log_info(WORKER, f"detail persistence failures: {detail_db_failures}")

        log_summary(
            WORKER,
            ok=detail_success_count,
            failed=failed_total,
            skipped=skipped_total or None,
        )



__all__ = ["run"]
