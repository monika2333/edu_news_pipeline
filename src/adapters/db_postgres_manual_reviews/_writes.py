from __future__ import annotations

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


__all__ = [
    "update_manual_review_statuses",
]
