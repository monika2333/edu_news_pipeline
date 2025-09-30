from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Sequence, Set, Tuple

from src.adapters.http_toutiao import (
    SUPABASE_ENV_DEFAULT,
    SupabaseConfig,
    build_supabase_config,
    fetch_article_records,
    fetch_existing_article_ids,
    fetch_feed_items,
    load_author_tokens,
    load_env_file,
    upload_records_to_supabase,
)
from src.config import get_settings
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "crawl"
DEFAULT_AUTHORS_FILE = Path("data/author_tokens.txt")
DEFAULT_LANG = "zh-CN,zh;q=0.9"
DEFAULT_TIMEOUT = 15
DEFAULT_SUPABASE_TABLE = "toutiao_articles"


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


def _resolve_supabase_env_path() -> Path:
    env_value = os.getenv("TOUTIAO_SUPABASE_ENV")
    if env_value:
        candidate = Path(env_value).expanduser()
        if not candidate.is_absolute():
            candidate = _repo_root() / candidate
        return candidate
    candidate = SUPABASE_ENV_DEFAULT
    if not candidate.is_absolute():
        candidate = _repo_root() / candidate
    return candidate


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


def _collect_article_records(
    feed_items,
    *,
    timeout: int,
    lang: Optional[str],
    existing_ids: Optional[Set[str]],
):
    return fetch_article_records(feed_items, timeout=timeout, lang=lang, existing_ids=existing_ids)


def _build_supabase_config(table: str, reset: bool, skip: bool) -> Optional[SupabaseConfig]:
    args = SimpleNamespace(
        supabase_table=table,
        reset_supabase_table=reset,
        skip_supabase_upload=skip,
    )
    return build_supabase_config(args)


def run(limit: int = 50, *, concurrency: Optional[int] = None) -> None:  # pylint: disable=unused-argument
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

    supabase_table = os.getenv("TOUTIAO_SUPABASE_TABLE", DEFAULT_SUPABASE_TABLE)
    supabase_reset = _truthy_env(os.getenv("TOUTIAO_SUPABASE_RESET"))
    supabase_skip = _truthy_env(os.getenv("TOUTIAO_SKIP_SUPABASE_UPLOAD"))

    env_path = _resolve_supabase_env_path()
    # Load env defaults; silently continue if files are missing.
    for candidate in {env_path, _repo_root() / ".env", _repo_root() / "config" / "abstract.env"}:
        try:
            load_env_file(candidate)
        except Exception as exc:  # pragma: no cover - best effort, log and continue
            log_error(WORKER, f"env:{candidate}", exc)

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

        config = _build_supabase_config(supabase_table, supabase_reset, supabase_skip)
        if config is None:
            log_info(WORKER, "Supabase upload is disabled or misconfigured; skipping crawl.")
            return

        try:
            existing_ids = fetch_existing_article_ids(config)
        except Exception as exc:
            log_error(WORKER, "supabase_existing", exc)
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

        try:
            records = _collect_article_records(
                feed_items,
                timeout=timeout_value,
                lang=lang,
                existing_ids=existing_ids,
            )
        except Exception as exc:
            log_error(WORKER, "article_content", exc)
            return

        if not records:
            log_info(WORKER, "No article content fetched.")
            return

        if effective_limit is not None:
            records_to_upload = records[:effective_limit]
        else:
            records_to_upload = records

        if not records_to_upload:
            log_info(WORKER, "No new records to upload after applying limit.")
            return

        skipped_count = len(records) - len(records_to_upload)

        if not upload_records_to_supabase(records_to_upload, config):
            log_error(WORKER, "supabase_upload", RuntimeError("Upload to toutiao_articles failed"))
            log_summary(WORKER, ok=0, failed=len(records_to_upload), skipped=skipped_count or None)
            return

        for record in records_to_upload:
            log_info(WORKER, f"OK {record.article_id}")

        log_summary(WORKER, ok=len(records_to_upload), failed=0, skipped=skipped_count or None)


__all__ = ["run"]
