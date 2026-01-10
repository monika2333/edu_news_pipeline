from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from src.adapters.db import get_adapter
from src.workers import log_info, log_summary, worker_session

WORKER = "hash-primary"
TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
SIMHASH_BITS = 64
SIMHASH_BAND_SIZE = 16
SIMHASH_BANDS = 4
SIMHASH_MASK = (1 << SIMHASH_BITS) - 1
MAX_TOKENS = 512
BAND_CANDIDATE_LIMIT = 50
HAMMING_THRESHOLD = 3


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


def _simhash_to_int(simhash_hex: Optional[str]) -> Optional[int]:
    if not simhash_hex:
        return None
    try:
        return int(simhash_hex, 16)
    except ValueError:
        return None


def _split_simhash(simhash_int: int) -> List[int]:
    bands: List[int] = []
    mask = (1 << SIMHASH_BAND_SIZE) - 1
    for index in range(SIMHASH_BANDS):
        shift = index * SIMHASH_BAND_SIZE
        bands.append((simhash_int >> shift) & mask)
    return bands


def _hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _to_signed_64(value: int) -> int:
    if value >= 1 << (SIMHASH_BITS - 1):
        return value - (1 << SIMHASH_BITS)
    return value


def _normalize_record(row: Mapping[str, Any]) -> Dict[str, Any]:
    record = dict(row)
    record["article_id"] = str(row.get("article_id") or "").strip()
    return record


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


def _process_features(
    candidates: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], int]:
    feature_updates: List[Dict[str, Any]] = []
    article_info: Dict[str, Dict[str, Any]] = {}
    skipped = 0

    for row in candidates:
        article_id = str(row.get("article_id") or "").strip()
        content = str(row.get("content_markdown") or "").strip()
        if not article_id or not content:
            skipped += 1
            continue
        content_hash = _compute_content_hash(content)
        simhash = _compute_simhash(content)
        simhash_unsigned = _simhash_to_int(simhash)
        simhash_signed = _to_signed_64(simhash_unsigned) if simhash_unsigned is not None else None
        bands = _split_simhash(simhash_unsigned) if simhash_unsigned is not None else [None] * SIMHASH_BANDS
        feature_updates.append(
            {
                "article_id": article_id,
                "content_hash": content_hash,
                "simhash": simhash,
                "simhash_bigint": simhash_signed,
                "simhash_band1": bands[0] if bands[0] is not None else None,
                "simhash_band2": bands[1] if bands[1] is not None else None,
                "simhash_band3": bands[2] if bands[2] is not None else None,
                "simhash_band4": bands[3] if bands[3] is not None else None,
            }
        )
        info = dict(row)
        info.update(
            {
                "article_id": article_id,
                "content_hash": content_hash,
                "simhash": simhash,
                "simhash_bigint": simhash_signed,
                "simhash_unsigned": simhash_unsigned,
                "bands": bands,
            }
        )
        article_info[article_id] = info
    return feature_updates, article_info, skipped


def _find_related_candidates(
    article_id: str,
    simhash_unsigned: Optional[int],
    bands: List[Optional[int]],
    content_hash: Optional[str],
    adapter: Any,
) -> List[Dict[str, Any]]:
    candidates_map: Dict[str, Dict[str, Any]] = {}
    
    if simhash_unsigned is not None:
        for index, band_value in enumerate(bands, start=1):
            if band_value is None:
                continue
            rows = adapter.fetch_filtered_articles_by_band(index, band_value, BAND_CANDIDATE_LIMIT)
            for candidate_row in rows:
                candidate_id = str(candidate_row.get("article_id") or "").strip()
                if not candidate_id or candidate_id == article_id:
                    continue
                candidates_map.setdefault(candidate_id, candidate_row)
        return list(candidates_map.values())
    
    if content_hash:
        return adapter.fetch_filtered_articles_by_hashes([content_hash])
        
    return []


def _process_grouping(
    candidates: List[Dict[str, Any]],
    article_info: Dict[str, Dict[str, Any]],
    adapter: Any
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    primary_updates: List[Dict[str, Any]] = []
    primary_rows: List[Dict[str, Any]] = []
    seen_primary_ids: Set[str] = set()
    duplicate_count = 0
    processed_ids: Set[str] = set()

    for row in candidates:
        article_id = str(row.get("article_id") or "").strip()
        if not article_id or article_id in processed_ids:
            continue
        info = article_info.get(article_id) or {}
        simhash_unsigned = info.get("simhash_unsigned")
        bands = info.get("bands") or [None] * SIMHASH_BANDS
        content_hash = info.get("content_hash")

        current_record = _normalize_record(info)
        group_records: List[Dict[str, Any]] = []
        
        related_candidates = _find_related_candidates(article_id, simhash_unsigned, bands, content_hash, adapter)

        if simhash_unsigned is not None:
            group_records.append(current_record)
            for candidate_row in related_candidates:
                candidate_record = _normalize_record(candidate_row)
                candidate_simhash_signed = candidate_record.get("simhash_bigint")
                if candidate_simhash_signed is None:
                    continue
                candidate_simhash = int(candidate_simhash_signed) & SIMHASH_MASK
                distance = _hamming_distance(int(simhash_unsigned), candidate_simhash)
                if distance <= HAMMING_THRESHOLD:
                    candidate_record["__distance"] = distance
                    group_records.append(candidate_record)
        else:
            if content_hash and related_candidates:
                for record in related_candidates:
                    candidate_record = _normalize_record(record)
                    group_records.append(candidate_record)
            if not group_records:
                group_records.append(current_record)

        # Ensure unique and include current record
        deduped: Dict[str, Dict[str, Any]] = {}
        for record in group_records:
            record_id = str(record.get("article_id") or "").strip()
            if not record_id:
                continue
            deduped.setdefault(record_id, record)
        group_records = list(deduped.values())

        if not group_records:
            continue

        primary_record = _choose_primary(group_records)
        if primary_record is None:
            continue
        primary_id = primary_record.get("article_id")
        if not primary_id:
            continue

        records_in_group = group_records
        duplicate_count += sum(1 for record in records_in_group if record.get("article_id") != primary_id)

        for record in records_in_group:
            record_id = record.get("article_id")
            if not record_id:
                continue
            desired_status = "primary" if record_id == primary_id else "duplicate"
            current_primary = record.get("primary_article_id")
            current_status = record.get("status")
            if current_primary != primary_id or current_status != desired_status:
                primary_updates.append(
                    {
                        "article_id": record_id,
                        "primary_article_id": primary_id,
                        "status": desired_status,
                    }
                )
        if primary_id not in seen_primary_ids:
            seen_primary_ids.add(primary_id)
            primary_rows.append(_prepare_primary_rows(records_in_group, primary_id))

        processed_ids.update(str(record.get("article_id") or "").strip() for record in records_in_group)
        
    return primary_updates, primary_rows, duplicate_count


def run(limit: int = 200) -> None:
    adapter = get_adapter()
    with worker_session(WORKER, limit=limit):
        candidates = adapter.fetch_filtered_articles_for_hashing(limit)
        if not candidates:
            log_summary(WORKER, ok=0, failed=0, skipped=0)
            return
        log_info(WORKER, f"fetched {len(candidates)} filtered articles for hashing")

        feature_updates, article_info, skipped = _process_features(candidates)

        if not feature_updates:
            log_summary(WORKER, ok=0, failed=0, skipped=skipped)
            return

        adapter.update_filtered_article_features(feature_updates)

        primary_updates, primary_rows, duplicate_count = _process_grouping(candidates, article_info, adapter)

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
