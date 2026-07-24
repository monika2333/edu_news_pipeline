from __future__ import annotations

from typing import Any, Mapping, Optional

import psycopg
from psycopg.types.json import Jsonb

from src.adapters.db_postgres_shared import json_safe


def insert_review_event(
    cur: psycopg.Cursor,
    *,
    actor_user_id: Optional[str],
    action: str,
    target_type: str,
    target_id: Optional[str],
    before_data: Optional[Mapping[str, Any]],
    after_data: Optional[Mapping[str, Any]],
    request_id: Optional[str] = None,
) -> int:
    cur.execute(
        """
        INSERT INTO review_events (
            actor_user_id,
            action,
            target_type,
            target_id,
            before_data,
            after_data,
            request_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            actor_user_id,
            action,
            target_type,
            target_id,
            Jsonb(json_safe(dict(before_data))) if before_data is not None else None,
            Jsonb(json_safe(dict(after_data))) if after_data is not None else None,
            request_id,
        ),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to record review event")
    return int(row["id"])


def fetch_review_events(
    cur: psycopg.Cursor,
    *,
    limit: int,
    offset: int,
    actor_user_id: Optional[str] = None,
    target_type: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int]:
    bounded_limit = max(1, min(limit, 200))
    bounded_offset = max(0, offset)
    clauses: list[str] = []
    params: list[Any] = []
    if actor_user_id:
        clauses.append("e.actor_user_id = %s")
        params.append(actor_user_id)
    if target_type:
        clauses.append("e.target_type = %s")
        params.append(target_type)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    cur.execute(
        f"""
        SELECT count(*) AS total
        FROM review_events e
        {where_sql}
        """,
        tuple(params),
    )
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    cur.execute(
        f"""
        SELECT
            e.id,
            e.actor_user_id,
            u.username AS actor_username,
            u.display_name AS actor_display_name,
            e.action,
            e.target_type,
            e.target_id,
            e.before_data,
            e.after_data,
            e.request_id,
            e.created_at
        FROM review_events e
        LEFT JOIN console_users u ON u.id = e.actor_user_id
        {where_sql}
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params + [bounded_limit, bounded_offset]),
    )
    return [dict(row) for row in cur.fetchall()], total


__all__ = ["fetch_review_events", "insert_review_event"]
