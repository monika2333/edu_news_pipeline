from __future__ import annotations

import asyncio
import os
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Set, Tuple, TypedDict

from src.adapters.db_postgres_core import get_adapter
from src.adapters.http_beijinghao import (
    ColumnEntry,
    build_detail_update as beijinghao_build_detail_update,
    feed_item_to_row as beijinghao_feed_item_to_row,
    fetch_detail as beijinghao_fetch_detail,
    list_items as beijinghao_list_items,
    make_article_id as beijinghao_make_article_id,
)
from src.adapters.http_bjrb import (
    DEFAULT_DELAY as BJRB_DEFAULT_DELAY,
    DEFAULT_TIMEOUT as BJRB_DEFAULT_TIMEOUT,
    BjrbIssueItem,
    article_to_detail_row as bjrb_article_to_detail_row,
    article_to_feed_row as bjrb_article_to_feed_row,
    fetch_article as bjrb_fetch_article,
    list_issue_items as bjrb_list_issue_items,
)
from src.adapters.http_btime import (
    UidEntry,
    build_detail_update as btime_build_detail_update,
    feed_item_to_row as btime_feed_item_to_row,
    fetch_detail as btime_fetch_detail,
    list_items as btime_list_items,
    make_article_id as btime_make_article_id,
)
from src.adapters.http_chinadaily import (
    build_detail_update as cd_build_detail_update,
    feed_item_to_row as cd_feed_item_to_row,
    fetch_detail as cd_fetch_detail,
    list_items as cd_list_items,
    make_article_id as cd_make_article_id,
)
from src.adapters.http_chinaeducationdaily import (
    FeedItemLike as JYBFeedItem,
    build_detail_update as jyb_build_detail_update,
    feed_item_to_row as jyb_feed_item_to_row,
    fetch_detail as jyb_fetch_detail,
    is_detail_url as jyb_is_detail_url,
    list_items as jyb_list_items,
    make_article_id as jyb_make_article_id,
)
from src.adapters.http_chinanews import (
    build_detail_update as cn_build_detail_update,
    feed_item_to_row as cn_feed_item_to_row,
    fetch_detail as cn_fetch_detail,
    list_items as cn_list_items,
    make_article_id as cn_make_article_id,
)
from src.adapters.http_chinanews_xj import (
    build_detail_update as cn_xj_build_detail_update,
    feed_item_to_row as cn_xj_feed_item_to_row,
    fetch_detail as cn_xj_fetch_detail,
    list_items as cn_xj_list_items,
    make_article_id as cn_xj_make_article_id,
)
from src.adapters.http_gmw import (
    DEFAULT_BASE_URL as GMW_DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT as GMW_DEFAULT_TIMEOUT,
    article_to_detail_row as gmw_article_to_detail_row,
    article_to_feed_row as gmw_article_to_feed_row,
    fetch_articles as gmw_fetch_articles,
    make_article_id as gmw_make_article_id,
)
from src.adapters.http_laodongwubao import (
    article_to_detail_row as ldwb_article_to_detail_row,
    article_to_feed_row as ldwb_article_to_feed_row,
    crawl_latest_issue as ldwb_crawl_latest_issue,
)
from src.adapters.http_qianlong import (
    DEFAULT_BASE_URLS as QIANLONG_DEFAULT_BASE_URLS,
    DEFAULT_DELAY as QIANLONG_DEFAULT_DELAY,
    DEFAULT_MAX_PAGES as QIANLONG_DEFAULT_MAX_PAGES,
    DEFAULT_TIMEOUT as QIANLONG_DEFAULT_TIMEOUT,
    article_to_detail_row as qianlong_article_to_detail_row,
    article_to_feed_row as qianlong_article_to_feed_row,
    fetch_articles as qianlong_fetch_articles,
    make_article_id as qianlong_make_article_id,
)
from src.adapters.http_tencent import (
    DEFAULT_MAX_PAGES as TENCENT_DEFAULT_MAX_PAGES,
    AuthorEntry as TencentAuthorEntry,
    build_detail_update as tencent_build_detail_update,
    feed_item_to_row as tencent_feed_item_to_row,
    fetch_article_detail as tencent_fetch_article_detail,
    list_feed_items as tencent_list_feed_items,
)
from src.adapters.http_toutiao import (
    FeedItem,
    build_detail_update as tt_build_detail_update,
    fetch_feed_items,
    fetch_info,
    feed_item_to_row,
    resolve_article_id_from_feed,
)
from src.business_config import (
    CrawlAccount,
    get_business_config,
    normalize_source_list,
)
from src.config import get_settings
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "crawl"
DEFAULT_LANG = "zh-CN,zh;q=0.9"
DEFAULT_TIMEOUT = 15


class CrawlStats(TypedDict):
    consumed: int
    ok: int
    failed: int
    skipped: int


ListItems = Callable[[Optional[int], Set[str]], Sequence[Any]]
PrepareFeed = Callable[[Any, datetime], Tuple[str, Dict[str, Any]]]
FetchDetail = Callable[[Any, str, datetime], Dict[str, Any]]


@dataclass(frozen=True)
class SourceFlow:
    """Source-specific callbacks and the small policy differences around them."""

    source: str
    display_name: str
    list_items: ListItems
    prepare_feed: PrepareFeed
    fetch_detail: FetchDetail
    details_in_list: bool = False
    load_existing_ids: bool = True
    skip_existing_ids: bool = False
    count_prepare_errors: bool = False
    count_feed_errors: bool = True
    continue_after_feed_error: bool = False
    missing_ids_fallback: Literal["raise", "all", "none"] = "raise"
    detail_delay: float = 0.0
    delay_after_failure: bool = False
    delay_after_last: bool = False


@dataclass(frozen=True)
class LinkedPageConfig:
    """Data-only wiring for sources that share the linked-page flow."""

    source: str
    display_name: str
    list_items_name: str
    make_article_id_name: str
    feed_item_to_row_name: str
    fetch_detail_name: str
    build_detail_update_name: str
    account_source: Optional[str] = None


@dataclass(frozen=True)
class SourceRunContext:
    """Values shared by every registered source invocation."""

    adapter: Any
    keywords: Sequence[str]
    remaining_limit: Optional[int]
    pages: Optional[int]
    accounts: Mapping[str, tuple[CrawlAccount, ...]]


RunnerKwargs = Callable[[SourceRunContext], Dict[str, Any]]


@dataclass(frozen=True)
class SourceRegistration:
    """A source's dispatch target, aliases, and source-local configuration."""

    runner_name: str
    kwargs_factory: RunnerKwargs
    aliases: Tuple[str, ...] = ()
    linked_page: Optional[LinkedPageConfig] = None

    def run(self, context: SourceRunContext) -> CrawlStats:
        runner = globals()[self.runner_name]
        kwargs = {
            "adapter": context.adapter,
            "keywords": context.keywords,
            "remaining_limit": context.remaining_limit,
            **self.kwargs_factory(context),
        }
        if self.linked_page is not None:
            kwargs["config"] = self.linked_page
        return runner(**kwargs)


def _empty_stats() -> CrawlStats:
    return {"consumed": 0, "ok": 0, "failed": 0, "skipped": 0}


def _truthy_env(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def _dedupe_keywords(hits: Sequence[str]) -> List[str]:
    ordered: List[str] = []
    seen: Set[str] = set()
    for kw in hits:
        if not kw:
            continue
        normalized = kw.strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _build_filtered_candidate(
    row: Mapping[str, Any],
    *,
    content: str,
    keywords: Sequence[str],
) -> Optional[Dict[str, Any]]:
    article_id = str(row.get("article_id") or "").strip()
    normalized_content = str(content or "").strip()
    if not article_id or not normalized_content:
        return None
    return {
        "article_id": article_id,
        "keywords": _dedupe_keywords(keywords),
        "status": "pending",
        "title": row.get("title"),
        "source": row.get("source"),
        "publish_time": row.get("publish_time"),
        "publish_time_iso": row.get("publish_time_iso"),
        "url": row.get("url"),
        "content_markdown": normalized_content,
    }


def _persist_filtered_candidates(
    adapter: Any,
    candidates: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> int:
    if not candidates:
        return 0
    try:
        adapter.ingest.upsert_filtered(candidates)
    except Exception as exc:  # pylint: disable=broad-except
        log_error(WORKER, f"{source}_filtered_articles", exc)
        return 0
    log_info(WORKER, f"{source} filtered articles queued: {len(candidates)}")
    return len(candidates)


def _queue_filtered_rows(
    adapter: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    source: str,
    keywords: Sequence[str],
) -> None:
    candidates: List[Dict[str, Any]] = []
    for row in rows:
        content = str(row.get("content_markdown") or "").strip()
        matched, hits = _contains_keywords(content, keywords)
        if not matched:
            continue
        candidate = _build_filtered_candidate(row, content=content, keywords=hits)
        if candidate:
            candidates.append(candidate)
    _persist_filtered_candidates(adapter, candidates, source=source)


def _resolve_missing_ids(
    adapter: Any,
    flow: SourceFlow,
    article_ids: Sequence[str],
) -> Set[str]:
    try:
        return set(adapter.ingest.get_raw_ids_missing_content(article_ids))
    except Exception as exc:
        if flow.missing_ids_fallback == "raise":
            raise
        log_error(WORKER, f"{flow.source}_missing_content", exc)
        if flow.missing_ids_fallback == "all":
            return set(article_ids)
        return set()


def _run_source_flow(
    *,
    adapter: Any,
    flow: SourceFlow,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
) -> CrawlStats:
    """Run the shared list -> feed -> detail -> filter persistence pipeline."""

    stats = _empty_stats()
    existing_ids: Set[str] = set()
    if flow.load_existing_ids:
        try:
            existing_ids = set(adapter.ingest.get_existing_raw_ids() or [])
        except Exception as exc:
            log_error(WORKER, f"{flow.source}_existing_ids", exc)

    try:
        items = flow.list_items(remaining_limit, existing_ids)
    except Exception as exc:
        log_error(WORKER, f"{flow.source}_list", exc)
        return stats
    if not items:
        log_info(WORKER, f"No articles returned from {flow.display_name}.")
        return stats

    feed_rows: List[Dict[str, Any]] = []
    index: Dict[str, Any] = {}
    embedded_detail_rows: Dict[str, Dict[str, Any]] = {}
    seen_ids: Set[str] = set()
    duplicates = 0
    existing_skips = 0
    for item in items:
        fetched_at = datetime.now(timezone.utc)
        try:
            article_id, feed_row = flow.prepare_feed(item, fetched_at)
            article_id = str(article_id or "").strip()
            if not article_id:
                raise ValueError("Empty article_id encountered")
        except Exception as exc:
            log_error(WORKER, f"{flow.source}_feed_row", exc)
            if flow.count_prepare_errors:
                stats["failed"] += 1
            continue
        if article_id in seen_ids:
            duplicates += 1
            continue
        seen_ids.add(article_id)
        if flow.skip_existing_ids and article_id in existing_ids:
            existing_skips += 1
            continue
        feed_rows.append(feed_row)
        index[article_id] = item
        if flow.details_in_list:
            try:
                embedded_detail_rows[article_id] = flow.fetch_detail(item, article_id, fetched_at)
            except Exception as exc:
                stats["failed"] += 1
                log_error(WORKER, f"{flow.source}_detail_row:{article_id}", exc)

    stats["consumed"] = len(seen_ids)
    stats["skipped"] += duplicates + existing_skips
    if duplicates:
        log_info(WORKER, f"{flow.source} duplicate articles skipped: {duplicates}")
    if existing_skips:
        log_info(WORKER, f"{flow.source} existing articles skipped: {existing_skips}")
    if not feed_rows:
        log_info(WORKER, f"No {flow.display_name} rows to upsert after filtering.")
        return stats

    feed_failed = False
    try:
        upserted = adapter.ingest.upsert_raw_feed_rows(feed_rows)
    except Exception as exc:
        feed_failed = True
        if flow.count_feed_errors:
            stats["failed"] += len(feed_rows)
        log_error(WORKER, f"{flow.source}_postgres_feed", exc)
        if not flow.continue_after_feed_error:
            return stats
    else:
        log_info(WORKER, f"{flow.source} feed rows upserted: {upserted}")

    detail_rows: List[Dict[str, Any]] = []
    if flow.details_in_list:
        detail_rows = [
            embedded_detail_rows[article_id]
            for article_id in index
            if article_id in embedded_detail_rows
        ]
    elif not feed_failed:
        missing_ids = _resolve_missing_ids(adapter, flow, list(index.keys()))
        targets = [(article_id, index[article_id]) for article_id in index if article_id in missing_ids]
        already_populated = len(index) - len(targets)
        stats["skipped"] += already_populated
        if already_populated:
            log_info(WORKER, f"{flow.source} articles already populated: {already_populated}")
        if targets:
            log_info(WORKER, f"{flow.source} articles needing detail refresh: {len(targets)}")

        for position, (article_id, item) in enumerate(targets, start=1):
            succeeded = False
            try:
                detail_rows.append(
                    flow.fetch_detail(item, article_id, datetime.now(timezone.utc))
                )
                succeeded = True
            except Exception as exc:
                stats["failed"] += 1
                log_error(WORKER, f"{flow.source}_detail:{article_id}", exc)
            finally:
                should_delay = flow.detail_delay and (succeeded or flow.delay_after_failure)
                if should_delay and (position < len(targets) or flow.delay_after_last):
                    time.sleep(flow.detail_delay)

    if detail_rows:
        try:
            adapter.ingest.update_raw_details(detail_rows)
        except Exception as exc:
            stats["failed"] += len(detail_rows)
            log_error(WORKER, f"{flow.source}_postgres_detail", exc)
            detail_rows = []
        else:
            for row in detail_rows:
                log_info(WORKER, f"{flow.source.upper()} DETAIL OK {row['article_id']}")

    stats["ok"] = len(detail_rows)
    _queue_filtered_rows(adapter, detail_rows, source=flow.source, keywords=keywords)
    return stats


def _build_toutiao_detail_update(
    item: FeedItem,
    article_id: str,
    detail_payload: Mapping[str, Any],
    *,
    detail_fetched_at: datetime,
) -> Dict[str, Any]:
    detail_url = str(detail_payload.get("url") or item.article_url or "").strip()
    if detail_url and jyb_is_detail_url(detail_url):
        try:
            jyb_payload = jyb_fetch_detail(detail_url)
            jyb_item = JYBFeedItem(
                title=item.title,
                url=detail_url,
                section=item.source,
                publish_time_iso=item.publish_time_iso,
                raw=item.raw,
            )
            row = jyb_build_detail_update(
                jyb_item,
                article_id,
                jyb_payload,
                detail_fetched_at=detail_fetched_at,
            )
            row["token"] = item.token
            row["profile_url"] = item.profile_url
            row["summary"] = row.get("summary") or item.summary
            row["comment_count"] = item.comment_count
            row["digg_count"] = item.digg_count
            return row
        except Exception as exc:  # pylint: disable=broad-except
            log_error(WORKER, f"toutiao_jyb_detail:{article_id}", exc)

    return tt_build_detail_update(
        item,
        article_id,
        dict(detail_payload),
        detail_fetched_at=detail_fetched_at,
    )


def _linked_page_flow(
    *,
    source: str,
    display_name: str,
    list_items: ListItems,
    make_article_id: Callable[[str], str],
    feed_item_to_row_func: Callable[..., Dict[str, Any]],
    fetch_detail_func: Callable[[str], Mapping[str, Any]],
    build_detail_update_func: Callable[..., Dict[str, Any]],
) -> SourceFlow:
    def prepare_feed(item: Any, fetched_at: datetime) -> Tuple[str, Dict[str, Any]]:
        article_id = make_article_id(item.url)
        return article_id, feed_item_to_row_func(item, article_id, fetched_at=fetched_at)

    def fetch_detail_row(
        item: Any,
        article_id: str,
        detail_fetched_at: datetime,
    ) -> Dict[str, Any]:
        payload = fetch_detail_func(item.url)
        return build_detail_update_func(
            item,
            article_id,
            payload,
            detail_fetched_at=detail_fetched_at,
        )

    return SourceFlow(
        source=source,
        display_name=display_name,
        list_items=list_items,
        prepare_feed=prepare_feed,
        fetch_detail=fetch_detail_row,
        count_feed_errors=False,
    )


def _run_toutiao_flow(
    *,
    adapter: Any,
    accounts: Sequence[CrawlAccount],
    show_browser: bool,
    timeout_value: int,
    lang: str,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
) -> CrawlStats:
    def list_items(limit: Optional[int], existing_ids: Set[str]) -> Sequence[Any]:
        entries = [
            (account.normalized_identifier, account.profile_url)
            for account in accounts
        ]
        if not entries:
            log_info(WORKER, "Toutiao has no enabled accounts; skipped.")
            return []
        return _collect_feed(
            entries,
            limit,
            show_browser=show_browser,
            existing_ids=existing_ids,
        )

    def prepare_feed(item: FeedItem, fetched_at: datetime) -> Tuple[str, Dict[str, Any]]:
        article_id = resolve_article_id_from_feed(item)
        return article_id, feed_item_to_row(item, article_id, fetched_at=fetched_at)

    def fetch_detail_row(
        item: FeedItem,
        article_id: str,
        detail_fetched_at: datetime,
    ) -> Dict[str, Any]:
        payload = fetch_info(article_id, timeout=timeout_value, lang=lang)
        return _build_toutiao_detail_update(
            item,
            article_id,
            payload,
            detail_fetched_at=detail_fetched_at,
        )

    return _run_source_flow(
        adapter=adapter,
        flow=SourceFlow(
            source="toutiao",
            display_name="Toutiao",
            list_items=list_items,
            prepare_feed=prepare_feed,
            fetch_detail=fetch_detail_row,
            count_prepare_errors=True,
        ),
        keywords=keywords,
        remaining_limit=remaining_limit,
    )


def _run_tencent_flow(
    *,
    adapter: Any,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    pages: Optional[int],
    accounts: Sequence[CrawlAccount],
) -> CrawlStats:
    def list_items(limit: Optional[int], existing_ids: Set[str]) -> Sequence[Any]:
        entries = [
            TencentAuthorEntry(
                author_id=account.normalized_identifier,
                profile_url=account.profile_url,
                raw_source=account.original_input,
            )
            for account in accounts
        ]
        if not entries:
            log_info(WORKER, "Tencent has no enabled accounts; skipped.")
            return []
        return tencent_list_feed_items(
            entries,
            max_pages=pages if pages is not None else TENCENT_DEFAULT_MAX_PAGES,
            limit=limit,
            existing_ids=existing_ids,
        )

    def prepare_feed(item: Any, fetched_at: datetime) -> Tuple[str, Dict[str, Any]]:
        article_id = str(item.article_id or "").strip()
        return article_id, tencent_feed_item_to_row(item, fetched_at=fetched_at)

    def fetch_detail_row(
        item: Any,
        _article_id: str,
        detail_fetched_at: datetime,
    ) -> Dict[str, Any]:
        detail = tencent_fetch_article_detail(item)
        return tencent_build_detail_update(detail, detail_fetched_at=detail_fetched_at)

    detail_delay = max(0.0, _env_float("TENCENT_DETAIL_DELAY", 0.5))
    return _run_source_flow(
        adapter=adapter,
        flow=SourceFlow(
            source="tencent",
            display_name="Tencent",
            list_items=list_items,
            prepare_feed=prepare_feed,
            fetch_detail=fetch_detail_row,
            count_prepare_errors=True,
            missing_ids_fallback="none",
            detail_delay=detail_delay,
            delay_after_failure=True,
            delay_after_last=True,
        ),
        keywords=keywords,
        remaining_limit=remaining_limit,
    )


def _run_registered_linked_page_flow(
    *,
    adapter: Any,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    pages: Optional[int],
    config: LinkedPageConfig,
    accounts: Sequence[CrawlAccount] = (),
) -> CrawlStats:
    list_kwargs: Dict[str, Any] = {}
    if config.account_source == "btime":
        list_kwargs["entries"] = [
            UidEntry(
                uid=account.normalized_identifier,
                profile_url=account.profile_url,
                raw_source=account.original_input,
            )
            for account in accounts
        ]
    elif config.account_source == "beijinghao":
        list_kwargs["entries"] = [
            ColumnEntry(
                column_code=account.normalized_identifier,
                page_url=account.profile_url,
                raw_source=account.original_input,
            )
            for account in accounts
        ]

    if config.account_source and not list_kwargs["entries"]:
        log_info(
            WORKER,
            f"{config.display_name} has no enabled accounts; skipped.",
        )
        return _empty_stats()

    flow = _linked_page_flow(
        source=config.source,
        display_name=config.display_name,
        list_items=lambda limit, existing: globals()[config.list_items_name](
            limit=limit,
            pages=pages or 1,
            existing_ids=existing,
            **list_kwargs,
        ),
        make_article_id=globals()[config.make_article_id_name],
        feed_item_to_row_func=globals()[config.feed_item_to_row_name],
        fetch_detail_func=globals()[config.fetch_detail_name],
        build_detail_update_func=globals()[config.build_detail_update_name],
    )
    return _run_source_flow(
        adapter=adapter,
        flow=flow,
        keywords=keywords,
        remaining_limit=remaining_limit,
    )


def _run_gmw_flow(
    *,
    adapter: Any,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    base_url: str,
    timeout_value: float,
) -> CrawlStats:
    consecutive_stop = max(0, _env_int("GMW_EXISTING_CONSECUTIVE_STOP", 5))

    def prepare_feed(item: Any, fetched_at: datetime) -> Tuple[str, Dict[str, Any]]:
        article_id = gmw_make_article_id(item.url)
        return article_id, gmw_article_to_feed_row(item, article_id, fetched_at=fetched_at)

    return _run_source_flow(
        adapter=adapter,
        flow=SourceFlow(
            source="gmw",
            display_name="Guangming Daily",
            list_items=lambda limit, existing: gmw_fetch_articles(
                limit=limit,
                base_url=base_url,
                timeout=timeout_value,
                existing_ids=existing,
                consecutive_stop=consecutive_stop,
            ),
            prepare_feed=prepare_feed,
            fetch_detail=lambda item, article_id, fetched_at: gmw_article_to_detail_row(
                item,
                article_id,
                detail_fetched_at=fetched_at,
            ),
            details_in_list=True,
        ),
        keywords=keywords,
        remaining_limit=remaining_limit,
    )


def _run_ldwb_flow(
    *,
    adapter: Any,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
) -> CrawlStats:
    def prepare_feed(item: Any, fetched_at: datetime) -> Tuple[str, Dict[str, Any]]:
        article_id = str(item.article_id or "").strip()
        return article_id, ldwb_article_to_feed_row(item, fetched_at=fetched_at)

    return _run_source_flow(
        adapter=adapter,
        flow=SourceFlow(
            source="ldwb",
            display_name="Laodong Wubao",
            list_items=lambda limit, _existing: ldwb_crawl_latest_issue(limit=limit),
            prepare_feed=prepare_feed,
            fetch_detail=lambda item, _article_id, fetched_at: ldwb_article_to_detail_row(
                item,
                detail_fetched_at=fetched_at,
            ),
            details_in_list=True,
            skip_existing_ids=True,
            continue_after_feed_error=True,
        ),
        keywords=keywords,
        remaining_limit=remaining_limit,
    )


def _run_bjrb_flow(
    *,
    adapter: Any,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    timeout_value: float,
    delay_value: float,
) -> CrawlStats:
    def prepare_feed(item: BjrbIssueItem, fetched_at: datetime) -> Tuple[str, Dict[str, Any]]:
        article_id = str(item.article_id or "").strip()
        return article_id, bjrb_article_to_feed_row(item, fetched_at=fetched_at)

    def fetch_detail_row(
        item: BjrbIssueItem,
        _article_id: str,
        detail_fetched_at: datetime,
    ) -> Dict[str, Any]:
        article = bjrb_fetch_article(item, timeout=timeout_value)
        return bjrb_article_to_detail_row(article, detail_fetched_at=detail_fetched_at)

    return _run_source_flow(
        adapter=adapter,
        flow=SourceFlow(
            source="bjrb",
            display_name="Beijing Daily",
            list_items=lambda limit, _existing: bjrb_list_issue_items(
                limit=limit,
                timeout=timeout_value,
            ),
            prepare_feed=prepare_feed,
            fetch_detail=fetch_detail_row,
            load_existing_ids=False,
            count_prepare_errors=True,
            missing_ids_fallback="all",
            detail_delay=delay_value,
        ),
        keywords=keywords,
        remaining_limit=remaining_limit,
    )


def _run_qianlong_flow(
    *,
    adapter: Any,
    keywords: Sequence[str],
    remaining_limit: Optional[int],
    base_urls: Sequence[str],
    timeout_value: float,
    delay_value: float,
    pages_hint: Optional[int],
    consecutive_stop: Optional[int],
) -> CrawlStats:
    def prepare_feed(item: Any, fetched_at: datetime) -> Tuple[str, Dict[str, Any]]:
        article_id = qianlong_make_article_id(item.url)
        return article_id, qianlong_article_to_feed_row(item, article_id, fetched_at=fetched_at)

    return _run_source_flow(
        adapter=adapter,
        flow=SourceFlow(
            source="qianlong",
            display_name="Qianlong",
            list_items=lambda limit, existing: qianlong_fetch_articles(
                limit=limit,
                base_urls=base_urls,
                pages=pages_hint,
                timeout=timeout_value,
                delay=delay_value,
                existing_ids=existing,
                consecutive_stop=consecutive_stop,
            ),
            prepare_feed=prepare_feed,
            fetch_detail=lambda item, article_id, fetched_at: qianlong_article_to_detail_row(
                item,
                article_id,
                detail_fetched_at=fetched_at,
            ),
            details_in_list=True,
        ),
        keywords=keywords,
        remaining_limit=remaining_limit,
    )


def _no_runner_kwargs(_context: SourceRunContext) -> Dict[str, Any]:
    return {}


def _pages_runner_kwargs(context: SourceRunContext) -> Dict[str, Any]:
    return {"pages": context.pages}


def _account_pages_runner_kwargs(source: str) -> RunnerKwargs:
    def factory(context: SourceRunContext) -> Dict[str, Any]:
        return {
            "pages": context.pages,
            "accounts": context.accounts.get(source, ()),
        }

    return factory


def _toutiao_runner_kwargs(context: SourceRunContext) -> Dict[str, Any]:
    return {
        "accounts": context.accounts.get("toutiao", ()),
        "show_browser": _truthy_env(os.getenv("TOUTIAO_SHOW_BROWSER")),
        "timeout_value": _env_int("TOUTIAO_FETCH_TIMEOUT", DEFAULT_TIMEOUT),
        "lang": os.getenv("TOUTIAO_LANG", DEFAULT_LANG),
    }


def _gmw_runner_kwargs(_context: SourceRunContext) -> Dict[str, Any]:
    return {
        "base_url": _env_str("GMW_BASE_URL", GMW_DEFAULT_BASE_URL),
        "timeout_value": _env_float("GMW_TIMEOUT", GMW_DEFAULT_TIMEOUT),
    }


def _qianlong_runner_kwargs(context: SourceRunContext) -> Dict[str, Any]:
    base_url = _env_str("QIANLONG_BASE_URL", "")
    pages_value = os.getenv("QIANLONG_PAGES") or os.getenv("QIANLONG_MAX_PAGES")
    try:
        configured_pages = int(pages_value) if pages_value is not None else None
    except ValueError:
        configured_pages = None
    if configured_pages is not None and configured_pages <= 0:
        configured_pages = QIANLONG_DEFAULT_MAX_PAGES

    return {
        "base_urls": (base_url,) if base_url else QIANLONG_DEFAULT_BASE_URLS,
        "timeout_value": _env_float("QIANLONG_TIMEOUT", QIANLONG_DEFAULT_TIMEOUT),
        "delay_value": _env_float("QIANLONG_DELAY", QIANLONG_DEFAULT_DELAY),
        "pages_hint": context.pages if context.pages is not None else configured_pages,
        "consecutive_stop": max(
            0,
            _env_int("QIANLONG_EXISTING_CONSECUTIVE_STOP", 5),
        ),
    }


def _bjrb_runner_kwargs(_context: SourceRunContext) -> Dict[str, Any]:
    return {
        "timeout_value": _env_float("BJRB_TIMEOUT", BJRB_DEFAULT_TIMEOUT),
        "delay_value": max(0.0, _env_float("BJRB_DELAY", BJRB_DEFAULT_DELAY)),
    }


_SOURCE_REGISTRY: Dict[str, SourceRegistration] = {
    "beijinghao": SourceRegistration(
        runner_name="_run_registered_linked_page_flow",
        kwargs_factory=_account_pages_runner_kwargs("beijinghao"),
        linked_page=LinkedPageConfig(
            source="beijinghao",
            display_name="Beijinghao",
            list_items_name="beijinghao_list_items",
            make_article_id_name="beijinghao_make_article_id",
            feed_item_to_row_name="beijinghao_feed_item_to_row",
            fetch_detail_name="beijinghao_fetch_detail",
            build_detail_update_name="beijinghao_build_detail_update",
            account_source="beijinghao",
        ),
    ),
    "bjrb": SourceRegistration(
        runner_name="_run_bjrb_flow",
        kwargs_factory=_bjrb_runner_kwargs,
        aliases=("beijingdaily",),
    ),
    "btime": SourceRegistration(
        runner_name="_run_registered_linked_page_flow",
        kwargs_factory=_account_pages_runner_kwargs("btime"),
        linked_page=LinkedPageConfig(
            source="btime",
            display_name="Btime",
            list_items_name="btime_list_items",
            make_article_id_name="btime_make_article_id",
            feed_item_to_row_name="btime_feed_item_to_row",
            fetch_detail_name="btime_fetch_detail",
            build_detail_update_name="btime_build_detail_update",
            account_source="btime",
        ),
    ),
    "chinadaily": SourceRegistration(
        runner_name="_run_registered_linked_page_flow",
        kwargs_factory=_pages_runner_kwargs,
        linked_page=LinkedPageConfig(
            source="chinadaily",
            display_name="China Daily",
            list_items_name="cd_list_items",
            make_article_id_name="cd_make_article_id",
            feed_item_to_row_name="cd_feed_item_to_row",
            fetch_detail_name="cd_fetch_detail",
            build_detail_update_name="cd_build_detail_update",
        ),
    ),
    "chinanews": SourceRegistration(
        runner_name="_run_registered_linked_page_flow",
        kwargs_factory=_pages_runner_kwargs,
        linked_page=LinkedPageConfig(
            source="chinanews",
            display_name="ChinaNews",
            list_items_name="cn_list_items",
            make_article_id_name="cn_make_article_id",
            feed_item_to_row_name="cn_feed_item_to_row",
            fetch_detail_name="cn_fetch_detail",
            build_detail_update_name="cn_build_detail_update",
        ),
    ),
    "chinanews_xj": SourceRegistration(
        runner_name="_run_registered_linked_page_flow",
        kwargs_factory=_pages_runner_kwargs,
        linked_page=LinkedPageConfig(
            source="chinanews_xj",
            display_name="ChinaNews Xinjiang",
            list_items_name="cn_xj_list_items",
            make_article_id_name="cn_xj_make_article_id",
            feed_item_to_row_name="cn_xj_feed_item_to_row",
            fetch_detail_name="cn_xj_fetch_detail",
            build_detail_update_name="cn_xj_build_detail_update",
        ),
    ),
    "gmw": SourceRegistration(
        runner_name="_run_gmw_flow",
        kwargs_factory=_gmw_runner_kwargs,
    ),
    "jyb": SourceRegistration(
        runner_name="_run_registered_linked_page_flow",
        kwargs_factory=_pages_runner_kwargs,
        linked_page=LinkedPageConfig(
            source="jyb",
            display_name="JYB",
            list_items_name="jyb_list_items",
            make_article_id_name="jyb_make_article_id",
            feed_item_to_row_name="jyb_feed_item_to_row",
            fetch_detail_name="jyb_fetch_detail",
            build_detail_update_name="jyb_build_detail_update",
        ),
    ),
    "ldwb": SourceRegistration(
        runner_name="_run_ldwb_flow",
        kwargs_factory=_no_runner_kwargs,
        aliases=("laodongwubao",),
    ),
    "qianlong": SourceRegistration(
        runner_name="_run_qianlong_flow",
        kwargs_factory=_qianlong_runner_kwargs,
    ),
    "tencent": SourceRegistration(
        runner_name="_run_tencent_flow",
        kwargs_factory=_account_pages_runner_kwargs("tencent"),
        aliases=("qq",),
    ),
    "toutiao": SourceRegistration(
        runner_name="_run_toutiao_flow",
        kwargs_factory=_toutiao_runner_kwargs,
    ),
}


def _get_source_registration(source: str) -> Optional[SourceRegistration]:
    registration = _SOURCE_REGISTRY.get(source)
    if registration is not None:
        return registration
    return next(
        (
            candidate
            for candidate in _SOURCE_REGISTRY.values()
            if source in candidate.aliases
        ),
        None,
    )


def run(
    limit: int = 5000,
    *,
    concurrency: Optional[int] = None,
    sources: Optional[Sequence[str]] = None,
    pages: Optional[int] = None,
) -> list[str]:  # pylint: disable=unused-argument
    settings = get_settings()
    business_config = get_business_config()
    # Normalize selected sources preserving order
    if sources is None:
        selected_order = list(business_config.crawl_sources)
    elif isinstance(sources, str):
        selected_order = normalize_source_list(
            [s for s in sources.split(",") if s.strip()],
            allow_daily=True,
        )
    else:
        selected_order = normalize_source_list(sources, allow_daily=True)

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
    failed_sources: list[str] = []
    with worker_session(WORKER, limit=effective_limit):
        for source in selected_order:
            if remaining_limit is not None and remaining_limit <= 0:
                break
            registration = _get_source_registration(source)
            if registration is None:
                log_info(WORKER, f"Unknown source '{source}' skipped")
                stats = _empty_stats()
            else:
                try:
                    stats = registration.run(
                        SourceRunContext(
                            adapter=adapter,
                            keywords=keywords,
                            remaining_limit=remaining_limit,
                            pages=pages,
                            accounts=business_config.accounts,
                        )
                    )
                except Exception as exc:
                    log_error(WORKER, f"{source}_source", exc)
                    log_info(WORKER, traceback.format_exc().rstrip())
                    failed_sources.append(source)
                    stats = _empty_stats()
                    stats["failed"] = 1

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
    return failed_sources



__all__ = ["run"]





