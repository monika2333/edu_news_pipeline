from __future__ import annotations

from typing import Any, Dict, List, Optional

import psycopg

from src.adapters.db_postgres_manual_reviews._base import report_type_expr
from src.domain.report_type import normalize_report_type as normalize_report_type_value


def manual_review_status_counts(cur: psycopg.Cursor, *, report_type: Optional[str] = None) -> Dict[str, int]:
    type_expr = report_type_expr()
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    query = f"""
        SELECT
            COUNT(*) FILTER (WHERE status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE status = 'discarded') AS discarded,
            COUNT(*) FILTER (
                WHERE status = 'selected' AND {type_expr} = %s
            ) AS selected,
            COUNT(*) FILTER (
                WHERE status = 'backup' AND {type_expr} = %s
            ) AS backup,
            COUNT(*) FILTER (
                WHERE status = 'exported' AND {type_expr} = %s
            ) AS exported
        FROM manual_reviews
    """
    cur.execute(query, (normalized_report_type,) * 3)
    row = cur.fetchone() or {}
    return {
        "pending": int(row.get("pending") or 0),
        "selected": int(row.get("selected") or 0),
        "backup": int(row.get("backup") or 0),
        "discarded": int(row.get("discarded") or 0),
        "exported": int(row.get("exported") or 0),
    }


def manual_review_pending_count(cur: psycopg.Cursor, *, report_type: Optional[str] = None) -> int:
    clauses = ["mr.status = 'pending'", "ns.status = 'ready_for_export'"]
    params: List[Any] = []
    type_expr = report_type_expr("mr")
    normalized_report_type = normalize_report_type_value(report_type)
    if normalized_report_type:
        clauses.append(f"{type_expr} = %s")
        params.append(normalized_report_type)
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT COUNT(*) AS total
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
    """
    cur.execute(query, tuple(params))
    row = cur.fetchone() or {}
    try:
        return int(row.get("total") or 0)
    except Exception:
        return 0

__all__ = [
    "manual_review_pending_count",
    "manual_review_status_counts",
]
