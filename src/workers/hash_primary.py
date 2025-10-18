from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from src.adapters.db import get_adapter
from src.workers import log_info, log_summary, worker_session

WORKER = "hash-primary"
TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
SIMHASH_BITS = 64
MAX_TOKENS = 512


def _normalize_content(text: str) -> str:
    return " ".join(text.split())


def _compute_content_hash(text: str) -> str:
    normalized = _normalize_content(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _token_counter(text: str) -> Counter:
    tokens = TOKEN_PATTERN.findall(_normalize_content(text).lower())
    if not tokens:
        return Counter()
    if len(tokens) > MAX_TOKENS:
        tokens = tokens[:MAX_TOKENS]
    return Counter(tokens)


def _compute_simhash(text: str) -> Optional[str]:
    counts = _token_counter(text)
    if not counts:
        return None
    bits = [0] * SIMHASH_BITS
    for token, weight in counts.items():
        digest = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
        for index in range(SIMHASH_BITS):
            if digest & (1 << index):
                bits[index] += weight
            else:
                bits[index] -= weight
    value = 0
    for index, total in enumerate(bits):
        if total > 0:
            value |= 1 << index
    return f"{value:016x}"


def _normalized_datetime(value: Optional[datetime]) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.max.replace(tzinfo=timezone.utc)


def _normalized_publish_time(value: Optional[int]) -> int:
    if value is None:
        return sys.maxsize
    return int(value)


def _choose_primary(records: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not records:
        return None
    sorted_records = sorted(
        records,
        key=lambda record: (
            0 if record.get("primary_article_id") == record.get("article_id") else 1,
            _normalized_datetime(record.get("publish_time_iso")),
            _normalized_publish_time(record.get("publish_time")),
            _normalized_datetime(record.get("inserted_at")),
            str(record.get("article_id") or ""),
        ),
    )
    return sorted_records[0]


def _prepare_primary_rows(records: Iterable[Dict[str, Any]], primary_id: str) -> Dict[str, Any]:
    for record in records:
        if record.get("article_id") == primary_id:
            return {
                "article_id": primary_id,
                "primary_article_id": primary_id,
                "status": "pending",
                "title": record.get("title"),
                "source": record.get("source"),
                "publish_time": record.get("publish_time"),
                "publish_time_iso": record.get("publish_time_iso"),
                "url": record.get("url"),
                "content_markdown": record.get("content_markdown"),
                "keywords": record.get("keywords"),
                "content_hash": record.get("content_hash"),
                "simhash": record.get("simhash"),
            }
    raise ValueError(f"Primary article {primary_id} not found in record set")


def run(limit: int = 200) -> None:
    adapter = get_adapter()
    with worker_session(WORKER, limit=limit):
        candidates = adapter.fetch_filtered_articles_for_hashing(limit)
        if not candidates:
            log_summary(WORKER, ok=0, failed=0, skipped=0)
            return
        log_info(WORKER, f"fetched {len(candidates)} filtered articles for hashing")

        feature_updates: List[Dict[str, Any]] = []
        hash_values: List[str] = []
        skipped = 0

        for row in candidates:
            article_id = str(row.get("article_id") or "").strip()
            content = str(row.get("content_markdown") or "").strip()
            if not article_id or not content:
                skipped += 1
                continue
            content_hash = _compute_content_hash(content)
            simhash = _compute_simhash(content)
            feature_updates.append(
                {
                    "article_id": article_id,
                    "content_hash": content_hash,
                    "simhash": simhash,
                }
            )
            hash_values.append(content_hash)

        if not feature_updates:
            log_summary(WORKER, ok=0, failed=0, skipped=skipped)
            return

        adapter.update_filtered_article_features(feature_updates)

        grouped_records = adapter.fetch_filtered_articles_by_hashes(hash_values)
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for record in grouped_records:
            hash_value = record.get("content_hash")
            if not hash_value:
                continue
            groups.setdefault(hash_value, []).append(record)

        primary_updates: List[Dict[str, Any]] = []
        primary_rows: List[Dict[str, Any]] = []
        seen_primary_ids: Set[str] = set()
        duplicate_count = 0

        for hash_value, records in groups.items():
            primary_record = _choose_primary(records)
            if primary_record is None:
                continue
            primary_id = primary_record.get("article_id")
            if not primary_id:
                continue
            duplicate_count += sum(1 for record in records if record.get("article_id") != primary_id)
            for record in records:
                article_id = record.get("article_id")
                if not article_id:
                    continue
                new_status = "primary" if article_id == primary_id else "duplicate"
                if record.get("primary_article_id") == primary_id and record.get("status") == new_status:
                    continue
                primary_updates.append(
                    {
                        "article_id": article_id,
                        "primary_article_id": primary_id,
                        "status": new_status,
                    }
                )
            if primary_id not in seen_primary_ids:
                seen_primary_ids.add(primary_id)
                primary_rows.append(_prepare_primary_rows(records, primary_id))

        if primary_updates:
            adapter.update_filtered_primary_ids(primary_updates)
        if primary_rows:
            adapter.upsert_primary_articles(primary_rows)

        hashed_count = len(feature_updates)
        log_summary(WORKER, ok=hashed_count, failed=0, skipped=skipped)
        if primary_rows:
            log_info(WORKER, f"primary articles prepared: {len(primary_rows)}")
        if duplicate_count:
            log_info(WORKER, f"duplicates linked: {duplicate_count}")


__all__ = ["run"]
