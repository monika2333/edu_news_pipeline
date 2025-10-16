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
from src.adapters.http_chinanews import (
    FeedItemLike as CNFeedItem,
    list_items as cn_list_items,
    fetch_detail as cn_fetch_detail,
    feed_item_to_row as cn_feed_item_to_row,
    make_article_id as cn_make_article_id,
    build_detail_update as cn_build_detail_update,
)
from src.adapters.http_gmw import (
    DEFAULT_BASE_URL as GMW_DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT as GMW_DEFAULT_TIMEOUT,
    article_to_detail_row as gmw_article_to_detail_row,
    article_to_feed_row as gmw_article_to_feed_row,
    fetch_articles as gmw_fetch_articles,
    make_article_id as gmw_make_article_id,
)
from src.adapters.http_chinadaily import (
    FeedItemLike as CDLFeedItem,
    list_items as cd_list_items,
    fetch_detail as cd_fetch_detail,
    feed_item_to_row as cd_feed_item_to_row,
    make_article_id as cd_make_article_id,
    build_detail_update as cd_build_detail_update,
)
from src.adapters.http_chinaeducationdaily import (
    FeedItemLike as JYBFeedItem,
    list_items as jyb_list_items,
    fetch_detail as jyb_fetch_detail,
    feed_item_to_row as jyb_feed_item_to_row,
    make_article_id as jyb_make_article_id,
    build_detail_update as jyb_build_detail_update,
)

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


def _load_keywords(path: Path) -> List[str]:
    if not path.exists():
        return []
    keywords: List[str] = []
    for line in path.read_text(encoding='utf-8').splitlines():
        raw = line.strip()
        if raw and not raw.startswith('#'):
            keywords.append(raw)
    return keywords


def _contains_keywords(content: str, keywords: Sequence[str]) -> Tuple[bool, List[str]]:
    if not keywords:
        return True, []
    lowered = content.lower()
    hits: List[str] = []
    for kw in keywords:
        if kw and kw.lower() in lowered:
            hits.append(kw)
    return bool(hits), hits



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


def _run_toutiao_flow(
    *,
    adapter,
    authors_path: Path,
    show_browser: bool,
    timeout_value: int,
    lang: str,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
) -> Dict[str, Any]:
    stats = {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}
    if not authors_path.exists():
        log_info(WORKER, f"Author token file not found: {authors_path}")
        return stats
    try:
        entries = _load_author_entries(authors_path)
    except Exception as exc:
        log_error(WORKER, authors_path.as_posix(), exc)
        return stats
    if not entries:
        log_info(WORKER, "Author token list is empty.")
        return stats
    try:
        existing_ids = adapter.get_existing_raw_article_ids()
    except Exception as exc:
        log_error(WORKER, "local_existing", exc)
        existing_ids = set()
    feed_limit = remaining_limit
    try:
        feed_items = _collect_feed(entries, feed_limit, show_browser=show_browser, existing_ids=existing_ids)
    except Exception as exc:
        log_error(WORKER, "feed", exc)
        return stats
    if not feed_items:
        log_info(WORKER, "No feed items returned from Toutiao.")
        return stats
    feed_rows, feed_index, unresolved_count, duplicate_count = _prepare_feed_rows(feed_items)
    stats["consumed"] = len(feed_index)
    if not feed_rows:
        log_info(WORKER, "No feed rows to upsert after filtering.")
        stats["failed"] += unresolved_count
        stats["skipped"] += duplicate_count
        return stats
    try:
        feed_upserted = adapter.upsert_raw_feed_rows(feed_rows)
    except Exception as exc:
        log_error(WORKER, "postgres_feed", exc)
        stats["failed"] += len(feed_rows)
        return stats
    log_info(WORKER, f"feed rows upserted: {feed_upserted}")
    if duplicate_count:
        log_info(WORKER, f"duplicate feed items skipped: {duplicate_count}")
    if unresolved_count:
        log_info(WORKER, f"feed items missing article_id: {unresolved_count}")
    missing_content_ids = adapter.get_raw_articles_missing_content(list(feed_index.keys()))
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
            adapter.update_raw_article_details(detail_rows)
        except Exception as exc:
            detail_db_failures = len(detail_rows)
            detail_rows = []
            log_error(WORKER, "postgres_detail", exc)
        else:
            for row in detail_rows:
                log_info(WORKER, f"DETAIL OK {row['article_id']}")
    pending_inserted = 0
    if detail_rows:
        for row in detail_rows:
            article_id = str(row.get('article_id') or '').strip()
            content = str(row.get('content_markdown') or '').strip()
            if not article_id or not content:
                continue
            ok, hits = _contains_keywords(content, keywords)
            if not ok:
                continue
            summary_payload = {
                'article_id': article_id,
                'title': row.get('title'),
                'source': row.get('source'),
                'publish_time': row.get('publish_time'),
                'publish_time_iso': row.get('publish_time_iso'),
                'url': row.get('url'),
                'content_markdown': content,
            }
            try:
                adapter.insert_pending_summary(
                    summary_payload,
                    keywords=hits,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as exc:
                log_error(WORKER, f'pending_summary:{article_id}', exc)
            else:
                pending_inserted += 1
    if pending_inserted:
        log_info(WORKER, f'pending summaries queued: {pending_inserted}')
    detail_success_count = len(detail_rows)
    failed_total = detail_fetch_failures + detail_db_failures + unresolved_count
    skipped_total = already_complete + duplicate_count
    if detail_fetch_failures:
        log_info(WORKER, f"detail fetch failures: {detail_fetch_failures}")
    if detail_db_failures:
        log_info(WORKER, f"detail persistence failures: {detail_db_failures}")
    stats["ok"] = detail_success_count
    stats["failed"] += failed_total
    stats["skipped"] += skipped_total
    return stats
def _run_chinanews_flow(
    *,
    adapter,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    pages: Optional[int],
) -> Dict[str, Any]:
    """Run ChinaNews ingestion; return stats and consumption.

    Returns a dict with keys: consumed, ok, failed, skipped.
    """
    stats = {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}
    try:
        existing_ids = adapter.get_existing_raw_article_ids()
    except Exception as exc:
        log_error(WORKER, "local_existing", exc)
        existing_ids = set()

    try:
        items = cn_list_items(limit=remaining_limit, pages=pages or 1, existing_ids=existing_ids)
    except Exception as exc:
        log_error(WORKER, "chinanews_list", exc)
        return stats
    if not items:
        log_info(WORKER, "No feed items returned from ChinaNews.")
        return stats

    feed_rows: List[Dict[str, Any]] = []
    index: Dict[str, CNFeedItem] = {}
    duplicates = 0
    for it in items:
        try:
            aid = cn_make_article_id(it.url)
        except Exception as exc:
            log_error(WORKER, "cn_feed_id", exc)
            continue
        if aid in index:
            duplicates += 1
            continue
        feed_rows.append(cn_feed_item_to_row(it, aid, fetched_at=datetime.now(timezone.utc)))
        index[aid] = it

    stats["consumed"] = len(index)
    if not feed_rows:
        log_info(WORKER, "No ChinaNews rows to upsert after filtering.")
        stats["skipped"] = duplicates
        return stats

    try:
        upserted = adapter.upsert_raw_feed_rows(feed_rows)
    except Exception as exc:
        log_error(WORKER, "postgres_feed_cn", exc)
        return stats
    log_info(WORKER, f"chinanews feed rows upserted: {upserted}")
    if duplicates:
        log_info(WORKER, f"chinanews duplicate feed items skipped: {duplicates}")

    missing_ids = adapter.get_raw_articles_missing_content(list(index.keys()))
    targets = [(aid, index[aid]) for aid in index if aid in missing_ids]
    already = len(index) - len(targets)
    if already:
        log_info(WORKER, f"chinanews articles already populated: {already}")
    if targets:
        log_info(WORKER, f"chinanews articles needing detail refresh: {len(targets)}")

    detail_rows: List[Dict[str, Any]] = []
    for aid, it in targets:
        try:
            payload = cn_fetch_detail(it.url)
        except Exception as exc:
            stats["failed"] += 1
            log_error(WORKER, f"cn_detail_fetch:{aid}", exc)
            continue
        try:
            detail_rows.append(
                cn_build_detail_update(
                    it,
                    aid,
                    payload,
                    detail_fetched_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:
            stats["failed"] += 1
            log_error(WORKER, f"cn_build_update:{aid}", exc)

    if detail_rows:
        try:
            adapter.update_raw_article_details(detail_rows)
        except Exception as exc:
            stats["failed"] += len(detail_rows)
            log_error(WORKER, "postgres_detail_cn", exc)
            detail_rows = []
        else:
            for row in detail_rows:
                log_info(WORKER, f"CN DETAIL OK {row['article_id']}")

    pending = 0
    for row in detail_rows:
        aid = str(row.get('article_id') or '').strip()
        content = str(row.get('content_markdown') or '').strip()
        if not aid or not content:
            continue
        ok_hit, hits = _contains_keywords(content, keywords)
        if not ok_hit:
            continue
        summary_payload = {
            'article_id': aid,
            'title': row.get('title'),
            'source': row.get('source'),
            'publish_time': row.get('publish_time'),
            'publish_time_iso': row.get('publish_time_iso'),
            'url': row.get('url'),
            'content_markdown': content,
        }
        try:
            adapter.insert_pending_summary(summary_payload, keywords=hits, fetched_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            log_error(WORKER, f"cn_pending_summary:{aid}", exc)
        else:
            pending += 1
    if pending:
        log_info(WORKER, f"chinanews pending summaries queued: {pending}")

    stats["ok"] = len(detail_rows)
    stats["skipped"] += already + duplicates
    return stats


def _run_gmw_flow(
    *,
    adapter,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    base_url: str,
    timeout_value: float,
) -> Dict[str, Any]:
    stats = {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}
    # Load existing IDs to allow early-stop on consecutive existing items
    try:
        existing_ids = adapter.get_existing_raw_article_ids()
    except Exception as exc:
        log_error(WORKER, "gmw_local_existing", exc)
        existing_ids = set()

    # Read early-stop threshold from env (default 5; 0 disables early-stop)
    try:
        gmw_consecutive_stop = int(os.getenv("GMW_EXISTING_CONSECUTIVE_STOP", "5"))
    except Exception:
        gmw_consecutive_stop = 5
    if gmw_consecutive_stop < 0:
        gmw_consecutive_stop = 0

    try:
        articles = gmw_fetch_articles(
            limit=remaining_limit,
            base_url=base_url,
            timeout=timeout_value,
            existing_ids=existing_ids,
            consecutive_stop=gmw_consecutive_stop,
        )
    except Exception as exc:
        log_error(WORKER, "gmw_fetch", exc)
        return stats
    if not articles:
        log_info(WORKER, "No articles returned from Guangming Daily.")
        return stats

    feed_rows: List[Dict[str, Any]] = []
    detail_rows: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()
    duplicates = 0
    for article in articles:
        try:
            article_id = gmw_make_article_id(article.url)
        except Exception as exc:
            log_error(WORKER, "gmw_article_id", exc)
            continue
        if article_id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(article_id)
        fetched_at = datetime.now(timezone.utc)
        feed_rows.append(gmw_article_to_feed_row(article, article_id, fetched_at=fetched_at))
        detail_rows.append(gmw_article_to_detail_row(article, article_id, detail_fetched_at=datetime.now(timezone.utc)))

    stats["consumed"] = len(seen_ids)
    if duplicates:
        log_info(WORKER, f"gmw duplicate articles skipped: {duplicates}")
        stats["skipped"] += duplicates
    if not feed_rows:
        log_info(WORKER, "No Guangming Daily rows to upsert after filtering.")
        return stats

    try:
        inserted = adapter.upsert_raw_feed_rows(feed_rows)
    except Exception as exc:
        log_error(WORKER, "gmw_postgres_feed", exc)
        stats["failed"] += len(feed_rows)
        return stats
    log_info(WORKER, f"gmw feed rows upserted: {inserted}")

    try:
        adapter.update_raw_article_details(detail_rows)
    except Exception as exc:
        log_error(WORKER, "gmw_postgres_detail", exc)
        stats["failed"] += len(detail_rows)
        detail_rows = []
    else:
        for row in detail_rows:
            log_info(WORKER, f"GMW DETAIL OK {row['article_id']}")

    stats["ok"] = len(detail_rows)
    pending = 0
    fetched_iso = datetime.now(timezone.utc).isoformat()
    for row in detail_rows:
        article_id = str(row.get("article_id") or "").strip()
        content = str(row.get("content_markdown") or "").strip()
        if not article_id or not content:
            continue
        ok_hit, hits = _contains_keywords(content, keywords)
        if not ok_hit:
            continue
        payload = {
            "article_id": article_id,
            "title": row.get("title"),
            "source": row.get("source"),
            "publish_time": row.get("publish_time"),
            "publish_time_iso": row.get("publish_time_iso"),
            "url": row.get("url"),
            "content_markdown": content,
        }
        try:
            adapter.insert_pending_summary(payload, keywords=hits, fetched_at=fetched_iso)
        except Exception as exc:
            log_error(WORKER, f"gmw_pending_summary:{article_id}", exc)
        else:
            pending += 1
    if pending:
        log_info(WORKER, f"gmw pending summaries queued: {pending}")
    return stats


def _run_jyb_flow(
    *,
    adapter,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    pages: Optional[int],
) -> Dict[str, Any]:
    stats = {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}
    try:
        existing_ids = adapter.get_existing_raw_article_ids()
    except Exception as exc:
        log_error(WORKER, "jyb_local_existing", exc)
        existing_ids = set()

    try:
        items = jyb_list_items(limit=remaining_limit, pages=pages or 1, existing_ids=existing_ids)
    except Exception as exc:
        log_error(WORKER, "jyb_list", exc)
        return stats
    if not items:
        log_info(WORKER, "No feed items returned from JYB.")
        return stats

    feed_rows: List[Dict[str, Any]] = []
    index: Dict[str, JYBFeedItem] = {}
    duplicates = 0
    for it in items:
        try:
            aid = jyb_make_article_id(it.url)
        except Exception as exc:
            log_error(WORKER, "jyb_feed_id", exc)
            continue
        if aid in index:
            duplicates += 1
            continue
        feed_rows.append(jyb_feed_item_to_row(it, aid, fetched_at=datetime.now(timezone.utc)))
        index[aid] = it

    stats["consumed"] = len(index)
    if not feed_rows:
        log_info(WORKER, "No JYB rows to upsert after filtering.")
        stats["skipped"] = duplicates
        return stats

    try:
        upserted = adapter.upsert_raw_feed_rows(feed_rows)
    except Exception as exc:
        log_error(WORKER, "postgres_feed_jyb", exc)
        return stats
    log_info(WORKER, f"jyb feed rows upserted: {upserted}")
    if duplicates:
        log_info(WORKER, f"jyb duplicate feed items skipped: {duplicates}")

    missing_ids = adapter.get_raw_articles_missing_content(list(index.keys()))
    targets = [(aid, index[aid]) for aid in index if aid in missing_ids]
    already = len(index) - len(targets)
    if already:
        log_info(WORKER, f"jyb articles already populated: {already}")
    if targets:
        log_info(WORKER, f"jyb articles needing detail refresh: {len(targets)}")

    detail_rows: List[Dict[str, Any]] = []
    for aid, it in targets:
        try:
            payload = jyb_fetch_detail(it.url)
        except Exception as exc:
            stats["failed"] += 1
            log_error(WORKER, f"jyb_detail_fetch:{aid}", exc)
            continue
        try:
            detail_rows.append(
                jyb_build_detail_update(
                    it,
                    aid,
                    payload,
                    detail_fetched_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:
            stats["failed"] += 1
            log_error(WORKER, f"jyb_build_update:{aid}", exc)

    if detail_rows:
        try:
            adapter.update_raw_article_details(detail_rows)
        except Exception as exc:
            stats["failed"] += len(detail_rows)
            log_error(WORKER, "postgres_detail_jyb", exc)
            detail_rows = []
        else:
            for row in detail_rows:
                log_info(WORKER, f"JYB DETAIL OK {row['article_id']}")

    pending = 0
    for row in detail_rows:
        aid = str(row.get('article_id') or '').strip()
        content = str(row.get('content_markdown') or '').strip()
        if not aid or not content:
            continue
        ok_hit, hits = _contains_keywords(content, keywords)
        if not ok_hit:
            continue
        summary_payload = {
            'article_id': aid,
            'title': row.get('title'),
            'source': row.get('source'),
            'publish_time': row.get('publish_time'),
            'publish_time_iso': row.get('publish_time_iso'),
            'url': row.get('url'),
            'content_markdown': content,
        }
        try:
            adapter.insert_pending_summary(summary_payload, keywords=hits, fetched_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            log_error(WORKER, f"jyb_pending_summary:{aid}", exc)
        else:
            pending += 1
    if pending:
        log_info(WORKER, f"jyb pending summaries queued: {pending}")

    stats["ok"] = len(detail_rows)
    stats["skipped"] += already + duplicates
    return stats


def _run_chinadaily_flow(
    *,
    adapter,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    pages: Optional[int],
) -> Dict[str, Any]:
    """Run China Daily ingestion; return stats and consumption.

    Returns a dict with keys: consumed, ok, failed, skipped.
    """
    stats = {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}
    try:
        existing_ids = adapter.get_existing_raw_article_ids()
    except Exception as exc:
        log_error(WORKER, "chinadaily_local_existing", exc)
        existing_ids = set()

    try:
        items = cd_list_items(limit=remaining_limit, pages=pages or 1, existing_ids=existing_ids)
    except Exception as exc:
        log_error(WORKER, "chinadaily_list", exc)
        return stats
    if not items:
        log_info(WORKER, "No feed items returned from China Daily.")
        return stats

    feed_rows: List[Dict[str, Any]] = []
    index: Dict[str, CDLFeedItem] = {}
    duplicates = 0
    for it in items:
        try:
            aid = cd_make_article_id(it.url)
        except Exception as exc:
            log_error(WORKER, "cd_feed_id", exc)
            continue
        if aid in index:
            duplicates += 1
            continue
        feed_rows.append(cd_feed_item_to_row(it, aid, fetched_at=datetime.now(timezone.utc)))
        index[aid] = it

    stats["consumed"] = len(index)
    if not feed_rows:
        log_info(WORKER, "No China Daily rows to upsert after filtering.")
        stats["skipped"] = duplicates
        return stats

    try:
        upserted = adapter.upsert_raw_feed_rows(feed_rows)
    except Exception as exc:
        log_error(WORKER, "postgres_feed_chinadaily", exc)
        return stats
    log_info(WORKER, f"chinadaily feed rows upserted: {upserted}")
    if duplicates:
        log_info(WORKER, f"chinadaily duplicate feed items skipped: {duplicates}")

    missing_ids = adapter.get_raw_articles_missing_content(list(index.keys()))
    targets = [(aid, index[aid]) for aid in index if aid in missing_ids]
    already = len(index) - len(targets)
    if already:
        log_info(WORKER, f"chinadaily articles already populated: {already}")
    if targets:
        log_info(WORKER, f"chinadaily articles needing detail refresh: {len(targets)}")

    detail_rows: List[Dict[str, Any]] = []
    for aid, it in targets:
        try:
            payload = cd_fetch_detail(it.url)
        except Exception as exc:
            stats["failed"] += 1
            log_error(WORKER, f"cd_detail_fetch:{aid}", exc)
            continue
        try:
            detail_rows.append(
                cd_build_detail_update(
                    it,
                    aid,
                    payload,
                    detail_fetched_at=datetime.now(timezone.utc),
                )
            )
        except Exception as exc:
            stats["failed"] += 1
            log_error(WORKER, f"cd_build_update:{aid}", exc)

    if detail_rows:
        try:
            adapter.update_raw_article_details(detail_rows)
        except Exception as exc:
            stats["failed"] += len(detail_rows)
            log_error(WORKER, "postgres_detail_chinadaily", exc)
            detail_rows = []
        else:
            for row in detail_rows:
                log_info(WORKER, f"CD DETAIL OK {row['article_id']}")

    pending = 0
    for row in detail_rows:
        aid = str(row.get('article_id') or '').strip()
        content = str(row.get('content_markdown') or '').strip()
        if not aid or not content:
            continue
        ok_hit, hits = _contains_keywords(content, keywords)
        if not ok_hit:
            continue
        summary_payload = {
            'article_id': aid,
            'title': row.get('title'),
            'source': row.get('source'),
            'publish_time': row.get('publish_time'),
            'publish_time_iso': row.get('publish_time_iso'),
            'url': row.get('url'),
            'content_markdown': content,
        }
        try:
            adapter.insert_pending_summary(summary_payload, keywords=hits, fetched_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            log_error(WORKER, f"cd_pending_summary:{aid}", exc)
        else:
            pending += 1
    if pending:
        log_info(WORKER, f"chinadaily pending summaries queued: {pending}")

    stats["ok"] = len(detail_rows)
    stats["skipped"] += already + duplicates
    return stats


def run(limit: int = 5000, *, concurrency: Optional[int] = None, sources: Optional[Sequence[str]] = None, pages: Optional[int] = None) -> None:  # pylint: disable=unused-argument
    settings = get_settings()
    # Normalize selected sources preserving order
    if sources is None:
        selected_order = ["toutiao"]
    elif isinstance(sources, str):
        selected_order = [s.strip().lower() for s in sources.split(',') if s.strip()]
    else:
        selected_order = [str(s).strip().lower() for s in sources if str(s).strip()]

    authors_path = _resolve_authors_path()
    show_browser = _truthy_env(os.getenv("TOUTIAO_SHOW_BROWSER"))
    timeout_env = os.getenv("TOUTIAO_FETCH_TIMEOUT")
    try:
        timeout_value = int(timeout_env) if timeout_env is not None else DEFAULT_TIMEOUT
    except ValueError:
        timeout_value = DEFAULT_TIMEOUT
    lang = os.getenv("TOUTIAO_LANG", DEFAULT_LANG)

    gmw_base_url_env = os.getenv("GMW_BASE_URL")
    gmw_base_url = (gmw_base_url_env.strip() if gmw_base_url_env and gmw_base_url_env.strip() else GMW_DEFAULT_BASE_URL)
    gmw_timeout_env = os.getenv("GMW_TIMEOUT")
    try:
        gmw_timeout = float(gmw_timeout_env) if gmw_timeout_env is not None else GMW_DEFAULT_TIMEOUT
    except ValueError:
        gmw_timeout = GMW_DEFAULT_TIMEOUT

    keywords_path_value = getattr(settings, 'keywords_path', None)
    keywords_file: Optional[Path]
    if keywords_path_value:
        keywords_file = Path(keywords_path_value)
        if not keywords_file.is_absolute():
            keywords_file = _repo_root() / keywords_file
    else:
        keywords_file = None
    keywords = _load_keywords(keywords_file) if keywords_file else []

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
    # initialize remaining capacity for multi-source run
    remaining_limit = effective_limit
    adapter = get_adapter()
    total_ok = total_failed = total_skipped = 0
    with worker_session(WORKER, limit=effective_limit):
        for source in selected_order:
            if remaining_limit is not None and remaining_limit <= 0:
                break
            if source == 'chinanews':
                stats = _run_chinanews_flow(adapter=adapter, keywords=keywords, remaining_limit=remaining_limit, pages=pages)
            elif source == 'chinadaily':
                stats = _run_chinadaily_flow(adapter=adapter, keywords=keywords, remaining_limit=remaining_limit, pages=pages)
            elif source == 'jyb':
                stats = _run_jyb_flow(adapter=adapter, keywords=keywords, remaining_limit=remaining_limit, pages=pages)
            elif source == 'gmw':
                stats = _run_gmw_flow(
                    adapter=adapter,
                    keywords=keywords,
                    remaining_limit=remaining_limit,
                    base_url=gmw_base_url,
                    timeout_value=gmw_timeout,
                )
            elif source == 'toutiao':
                stats = _run_toutiao_flow(
                    adapter=adapter,
                    authors_path=authors_path,
                    show_browser=show_browser,
                    timeout_value=timeout_value,
                    lang=lang,
                    keywords=keywords,
                    remaining_limit=remaining_limit,
                )
            else:
                log_info(WORKER, f"Unknown source '{source}' skipped")
                stats = {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}

            try:
                consumed = int(stats.get('consumed') or 0)
            except Exception:
                consumed = 0
            if remaining_limit is not None:
                remaining_limit = max(0, int(remaining_limit) - consumed)
            total_ok += int(stats.get('ok') or 0)
            total_failed += int(stats.get('failed') or 0)
            total_skipped += int(stats.get('skipped') or 0)

        log_summary(WORKER, ok=total_ok, failed=(total_failed or None), skipped=(total_skipped or None))



__all__ = ["run"]





