from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from src.adapters import db_postgres
from src.adapters.db import get_adapter
from src.config import get_settings
from src.workers import log_info, log_summary, worker_session

WORKER = "keyword_filter"
DEFAULT_BATCH_SIZE = 200


def _load_keywords(path) -> List[str]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    keywords: List[str] = []
    for line in content.splitlines():
        token = line.strip()
        if token and not token.startswith("#") and token not in keywords:
            keywords.append(token)
    return keywords


def _contains_keywords(text: str, keywords: Sequence[str]) -> List[str]:
    if not keywords:
        return []
    lowered = text.lower()
    hits: List[str] = []
    for kw in keywords:
        if kw and kw.lower() in lowered and kw not in hits:
            hits.append(kw)
    return hits


def run(
    *,
    limit: Optional[int] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    since_updated: Optional[datetime] = None,
    article_ids: Optional[Sequence[str]] = None,
    dry_run: bool = False,
) -> Dict[str, int]:
    settings = get_settings()
    adapter = get_adapter()

    limit_value: Optional[int] = limit
    if limit_value is None and settings.process_limit:
        limit_value = settings.process_limit

    batch_size_value = max(1, batch_size or DEFAULT_BATCH_SIZE)

    keywords_path = settings.keywords_path
    keywords = _load_keywords(keywords_path)
    if not keywords:
        log_info(WORKER, f"No keywords configured at {keywords_path}; skipping.")
        return {"processed": 0, "matched": 0, "inserted": 0}

    processed = 0
    matched = 0
    inserted = 0
    now = datetime.now(timezone.utc)

    with worker_session(WORKER, limit=limit_value):
        rows_iter = adapter.iter_raw_articles_for_filtered_backfill(
            since=since_updated,
            article_ids=article_ids,
            batch_size=batch_size_value,
            limit=limit_value,
            only_missing=not article_ids,
        )
        for batch in rows_iter:
            payload: List[Dict[str, object]] = []
            for row in batch:
                processed += 1
                article_id = str(row.get("article_id") or "").strip()
                if not article_id:
                    continue
                content = str(row.get("content_markdown") or "")
                if not content.strip():
                    continue
                hits = _contains_keywords(content, keywords)
                if not hits:
                    continue
                matched += 1
                content_hash = row.get("content_hash")
                fingerprint = row.get("fingerprint")
                if not content_hash or not fingerprint:
                    content_hash, fingerprint = db_postgres._compute_content_features(content)
                payload.append(
                    {
                        "article_id": article_id,
                        "primary_article_id": row.get("primary_article_id") or article_id,
                        "keywords": hits,
                        "title": row.get("title"),
                        "source": row.get("source"),
                        "publish_time": row.get("publish_time"),
                        "publish_time_iso": row.get("publish_time_iso"),
                        "url": row.get("url"),
                        "content_markdown": content,
                        "content_hash": content_hash,
                        "fingerprint": fingerprint,
                        "sentiment_label": row.get("sentiment_label"),
                        "sentiment_confidence": row.get("sentiment_confidence"),
                        "inserted_at": now,
                        "updated_at": now,
                    }
                )
                if limit_value and processed >= limit_value:
                    break
            if payload and not dry_run:
                inserted += adapter.upsert_filtered_articles(payload)
                log_info(WORKER, f"Upserted {len(payload)} rows into filtered_articles (total_inserted={inserted})")
            elif payload:
                log_info(WORKER, f"[dry-run] would upsert {len(payload)} filtered_articles rows")
            if limit_value and processed >= limit_value:
                break

    log_summary(WORKER, ok=inserted if not dry_run else matched, failed=0, skipped=processed - matched)
    return {"processed": processed, "matched": matched, "inserted": inserted if not dry_run else 0}


__all__ = ["run"]

