from __future__ import annotations

from typing import Dict, Optional

import psycopg

from src.adapters.db_postgres_manual_reviews._base import report_type_expr
from src.domain.report_type import normalize_report_type as normalize_report_type_value


def manual_review_status_counts(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    report_type: Optional[str] = None,
) -> Dict[str, int]:
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
        WHERE owner_user_id = %s
    """
    cur.execute(query, (normalized_report_type,) * 3 + (owner_user_id,))
    row = cur.fetchone() or {}
    return {
        "pending": int(row.get("pending") or 0),
        "selected": int(row.get("selected") or 0),
        "backup": int(row.get("backup") or 0),
        "discarded": int(row.get("discarded") or 0),
        "exported": int(row.get("exported") or 0),
    }


__all__ = [
    "manual_review_status_counts",
]
