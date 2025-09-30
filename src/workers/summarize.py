from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.adapters.db_supabase import get_adapter
from src.adapters.llm_summary import summarise
from src.config import get_settings
from src.workers import log_error, log_info, log_summary, worker_session

WORKER = "summarize"
CURSOR_FILENAME = Path("data/summarize_cursor.json")
CURSOR_ENV_VAR = "SUMMARIZE_CURSOR_PATH"
DEFAULT_FETCH_MULTIPLIER = 4


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_cursor_path() -> Path:
    env_value = os.getenv(CURSOR_ENV_VAR)
    if env_value:
        candidate = Path(env_value).expanduser()
        if not candidate.is_absolute():
            candidate = _repo_root() / candidate
        return candidate
    if CURSOR_FILENAME.is_absolute():
        return CURSOR_FILENAME
    return _repo_root() / CURSOR_FILENAME


def _load_cursor(path: Path) -> Dict[str, Optional[str]]:
    if not path.exists():
        return {"fetched_at": None, "article_id": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"fetched_at": None, "article_id": None}
    return {
        "fetched_at": data.get("fetched_at"),
        "article_id": data.get("article_id"),
    }


def _save_cursor(path: Path, fetched_at: Optional[str], article_id: Optional[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": fetched_at, "article_id": article_id}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _load_keywords(path: Path) -> List[str]:
    if not path.exists():
        return []
    keywords: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if raw and not raw.startswith("#"):
            keywords.append(raw)
    return keywords


def _contains_keywords(text: str, keywords: Sequence[str]) -> Tuple[bool, List[str]]:
    if not keywords:
        return True, []
    lowered = text.lower()
    hits: List[str] = []
    for kw in keywords:
        if kw and kw.lower() in lowered:
            hits.append(kw)
    return (bool(hits), hits)


def _filter_articles_after_cursor(
    articles: Sequence[Dict[str, Any]],
    fetched_at: Optional[str],
    article_id: Optional[str],
) -> List[Dict[str, Any]]:
    ordered = sorted(
        list(articles),
        key=lambda item: (
            item.get("fetched_at") or "",
            str(item.get("article_id") or ""),
        ),
    )
    if not fetched_at:
        return ordered
    result: List[Dict[str, Any]] = []
    for article in ordered:
        article_fetched = article.get("fetched_at")
        article_fetch_str = article_fetched or ""
        if article_fetch_str and article_fetch_str < fetched_at:
            continue
        if (
            article_fetch_str == fetched_at
            and article_id
            and str(article.get("article_id") or "") <= article_id
        ):
            continue
        result.append(article)
    return result


def run(limit: int = 50, *, concurrency: Optional[int] = None, keywords_path: Optional[Path] = None) -> None:
    settings = get_settings()
    adapter = get_adapter()

    cursor_path = _resolve_cursor_path()
    cursor_state = _load_cursor(cursor_path)
    cursor_fetched_at = cursor_state.get("fetched_at")
    cursor_article_id = cursor_state.get("article_id")

    limit_value: Optional[int]
    if limit and limit > 0:
        limit_value = limit
    else:
        limit_value = None

    process_cap = settings.process_limit
    if process_cap is not None:
        if limit_value is None:
            limit_value = process_cap
        else:
            limit_value = min(limit_value, process_cap)

    fetch_target = limit_value or settings.default_concurrency or 5
    fetch_limit = max(1, fetch_target) * DEFAULT_FETCH_MULTIPLIER

    keywords_file = keywords_path or settings.keywords_path
    keywords = _load_keywords(Path(keywords_file))

    session_limit = limit_value or fetch_target

    with worker_session(WORKER, limit=session_limit):
        raw_articles = adapter.fetch_toutiao_articles_for_summary(
            after_fetched_at=cursor_fetched_at,
            limit=fetch_limit,
        )
        articles = _filter_articles_after_cursor(raw_articles, cursor_fetched_at, cursor_article_id)
        if not articles:
            log_info(WORKER, "No articles available for summarisation.")
            return

        article_ids = [str(item.get("article_id")) for item in articles if item.get("article_id")]
        existing_ids = adapter.get_existing_news_summary_ids(article_ids)

        success = 0
        failed = 0
        skipped = 0

        latest_fetched = cursor_fetched_at
        latest_article = cursor_article_id

        for article in articles:
            article_id = str(article.get("article_id") or "").strip()
            fetched_at_value = article.get("fetched_at")
            if not isinstance(fetched_at_value, str) and fetched_at_value is not None:
                fetched_at_value = str(fetched_at_value)

            if not article_id:
                skipped += 1
                latest_fetched = fetched_at_value or latest_fetched
                _save_cursor(cursor_path, latest_fetched, latest_article)
                continue

            if article_id in existing_ids:
                skipped += 1
                log_info(WORKER, f"Skip existing summary {article_id}")
                latest_fetched = fetched_at_value or latest_fetched
                latest_article = article_id
                _save_cursor(cursor_path, latest_fetched, latest_article)
                continue

            content = str(article.get("content_markdown") or "").strip()
            if not content:
                skipped += 1
                log_info(WORKER, f"Skip empty content {article_id}")
                latest_fetched = fetched_at_value or latest_fetched
                latest_article = article_id
                _save_cursor(cursor_path, latest_fetched, latest_article)
                continue

            ok, hits = _contains_keywords(content, keywords)
            if not ok:
                skipped += 1
                latest_fetched = fetched_at_value or latest_fetched
                latest_article = article_id
                _save_cursor(cursor_path, latest_fetched, latest_article)
                continue

            summary_payload = {
                "title": article.get("title"),
                "content": content,
            }

            try:
                result = summarise(summary_payload)
                summary_text = result.get("summary", "").strip()
                if not summary_text:
                    raise RuntimeError("Summarisation returned empty text")
                adapter.upsert_news_summary(
                    article,
                    summary_text,
                    keywords=hits,
                )
                success += 1
                existing_ids.add(article_id)
                log_info(WORKER, f"OK {article_id}")
            except Exception as exc:
                failed += 1
                log_error(WORKER, article_id, exc)
            finally:
                latest_fetched = fetched_at_value or latest_fetched
                latest_article = article_id
                _save_cursor(cursor_path, latest_fetched, latest_article)

            if limit_value is not None and success >= limit_value:
                break

        log_summary(WORKER, ok=success, failed=failed, skipped=skipped if skipped else None)


__all__ = ["run"]
