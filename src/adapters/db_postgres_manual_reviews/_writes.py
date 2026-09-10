from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple

import psycopg

from src.domain.report_type import normalize_report_type as normalize_report_type_value


def update_manual_review_statuses(
    cur: psycopg.Cursor,
    updates: Sequence[Mapping[str, Any]],
    *,
    report_type: Optional[str] = None,
) -> int:
    if not updates:
        return 0
    default_report_type = normalize_report_type_value(report_type)
    payload: List[Tuple[Any, ...]] = []
    for item in updates:
        article_id = str(item.get("article_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if not article_id or not status:
            continue
        target_report_type = normalize_report_type_value(item.get("report_type")) or default_report_type
        payload.append(
            (
                status,
                item.get("rank"),
                item.get("decided_by"),
                item.get("decided_at"),
                target_report_type,
                article_id,
            )
        )
    if not payload:
        return 0
    query = """
        UPDATE manual_reviews
        SET status = %s,
            rank = %s,
            decided_by = COALESCE(%s, decided_by),
            decided_at = COALESCE(%s, decided_at),
            report_type = COALESCE(%s, report_type),
            updated_at = NOW()
        WHERE article_id = %s
    """
    cur.executemany(query, payload)
    return cur.rowcount


def reset_manual_reviews_to_pending(
    cur: psycopg.Cursor,
    article_ids: Sequence[str],
    *,
    actor: Optional[str] = None,
    decided_at: Optional[datetime] = None,
    report_type: Optional[str] = None,
) -> int:
    target_ids = [str(aid).strip() for aid in article_ids or [] if str(aid).strip()]
    if not target_ids:
        return 0
    timestamp = decided_at or datetime.now(timezone.utc)
    normalized_report_type = normalize_report_type_value(report_type)
    payload = [(actor, timestamp, normalized_report_type, aid) for aid in target_ids]
    query = """
        UPDATE manual_reviews
        SET status = 'pending',
            rank = NULL,
            decided_by = COALESCE(%s, decided_by),
            decided_at = %s,
            report_type = COALESCE(%s, report_type),
            updated_at = NOW()
        WHERE article_id = %s
    """
    cur.executemany(query, payload)
    return cur.rowcount


def update_manual_review_summaries(
    cur: psycopg.Cursor,
    edits: Mapping[str, Mapping[str, Any]],
    *,
    actor: Optional[str] = None,
    decided_at: Optional[datetime] = None,
    report_type: Optional[str] = None,
) -> int:
    if not edits:
        return 0
    timestamp = decided_at or datetime.now(timezone.utc)
    normalized_report_type = normalize_report_type_value(report_type)
    payload: List[Tuple[Any, ...]] = []
    for aid, edit in edits.items():
        summary = edit.get("summary")
        notes = edit.get("notes")
        score = edit.get("score")
        manual_llm_source = edit.get("manual_llm_source")
        item_report_type = normalize_report_type_value(edit.get("report_type")) or normalized_report_type
        article_id = str(aid).strip()
        if not article_id or (summary is None and manual_llm_source is None and notes is None and score is None):
            continue
        payload.append((summary, manual_llm_source, notes, score, actor, timestamp, item_report_type, article_id))
    if not payload:
        return 0
    query = """
        UPDATE manual_reviews
        SET summary = COALESCE(%s, summary),
            manual_llm_source = COALESCE(%s, manual_llm_source),
            notes = COALESCE(%s, notes),
            score = COALESCE(%s, score),
            decided_by = COALESCE(%s, decided_by),
            decided_at = COALESCE(%s, decided_at),
            report_type = COALESCE(%s, report_type),
            updated_at = NOW()
        WHERE article_id = %s
    """
    cur.executemany(query, payload)
    return cur.rowcount

__all__ = [
    "reset_manual_reviews_to_pending",
    "update_manual_review_statuses",
    "update_manual_review_summaries",
]
