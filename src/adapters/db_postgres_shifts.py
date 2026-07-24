from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

import psycopg


SHIFT_SELECT = """
    s.id,
    s.user_id,
    u.username,
    u.display_name,
    u.is_active AS user_is_active,
    s.starts_at,
    s.ends_at,
    s.cancelled_at,
    s.notes,
    s.created_by_user_id,
    s.created_at,
    s.updated_at
"""


def fetch_active_duty_editors(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, username, display_name, role, is_active
        FROM console_users
        WHERE role = 'duty_editor'
          AND is_active = true
        ORDER BY display_name, username
        """
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_duty_schedule(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            ds.id,
            ds.weekday,
            ds.user_id,
            u.username,
            u.display_name,
            u.is_active AS user_is_active,
            ds.updated_at
        FROM duty_schedules ds
        JOIN console_users u ON u.id = ds.user_id
        ORDER BY ds.weekday
        """
    )
    return [dict(row) for row in cur.fetchall()]


def upsert_duty_schedule(
    cur: psycopg.Cursor,
    assignments: Mapping[int, str],
) -> list[dict[str, Any]]:
    for weekday, user_id in sorted(assignments.items()):
        cur.execute(
            """
            INSERT INTO duty_schedules (weekday, user_id)
            VALUES (%s, %s)
            ON CONFLICT (weekday) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                updated_at = now()
            """,
            (weekday, user_id),
        )
    return fetch_duty_schedule(cur)


def insert_duty_shifts(
    cur: psycopg.Cursor,
    shifts: Sequence[Mapping[str, Any]],
) -> int:
    inserted = 0
    for shift in shifts:
        cur.execute(
            """
            INSERT INTO duty_shifts (
                user_id,
                starts_at,
                ends_at,
                created_by_user_id
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (starts_at) DO NOTHING
            """,
            (
                shift["user_id"],
                shift["starts_at"],
                shift["ends_at"],
                shift.get("created_by_user_id"),
            ),
        )
        inserted += cur.rowcount
    return inserted


def create_duty_shift(
    cur: psycopg.Cursor,
    *,
    user_id: str,
    starts_at: datetime,
    ends_at: datetime,
    notes: Optional[str],
    created_by_user_id: Optional[str],
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO duty_shifts (
            user_id,
            starts_at,
            ends_at,
            notes,
            created_by_user_id
        )
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id
        """,
        (user_id, starts_at, ends_at, notes, created_by_user_id),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to create duty shift")
    result = fetch_duty_shift(cur, str(row["id"]))
    if not result:
        raise RuntimeError("Created duty shift cannot be loaded")
    return result


def fetch_duty_shift(
    cur: psycopg.Cursor,
    shift_id: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT {SHIFT_SELECT}
        FROM duty_shifts s
        JOIN console_users u ON u.id = s.user_id
        WHERE s.id = %s
        """,
        (shift_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_duty_shifts(
    cur: psycopg.Cursor,
    *,
    user_id: Optional[str] = None,
    starts_before: Optional[datetime] = None,
    ends_after: Optional[datetime] = None,
    include_cancelled: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if user_id:
        clauses.append("s.user_id = %s")
        params.append(user_id)
    if starts_before:
        clauses.append("s.starts_at < %s")
        params.append(starts_before)
    if ends_after:
        clauses.append("s.ends_at > %s")
        params.append(ends_after)
    if not include_cancelled:
        clauses.append("s.cancelled_at IS NULL")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(limit, 500)))
    cur.execute(
        f"""
        SELECT {SHIFT_SELECT}
        FROM duty_shifts s
        JOIN console_users u ON u.id = s.user_id
        {where_sql}
        ORDER BY s.starts_at DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_overlapping_duty_shift(
    cur: psycopg.Cursor,
    *,
    starts_at: datetime,
    ends_at: datetime,
    exclude_shift_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    cur.execute(
        f"""
        SELECT {SHIFT_SELECT}
        FROM duty_shifts s
        JOIN console_users u ON u.id = s.user_id
        WHERE s.cancelled_at IS NULL
          AND s.starts_at < %s
          AND s.ends_at > %s
          AND (%s IS NULL OR s.id <> %s)
        ORDER BY s.starts_at
        LIMIT 1
        """,
        (ends_at, starts_at, exclude_shift_id, exclude_shift_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def update_duty_shift(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    user_id: Optional[str] = None,
    set_user_id: bool = False,
    notes: Optional[str] = None,
    set_notes: bool = False,
    cancelled: Optional[bool] = None,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        UPDATE duty_shifts
        SET user_id = CASE WHEN %s THEN %s ELSE user_id END,
            notes = CASE WHEN %s THEN %s ELSE notes END,
            cancelled_at = CASE
                WHEN %s IS TRUE THEN COALESCE(cancelled_at, now())
                WHEN %s IS FALSE THEN NULL
                ELSE cancelled_at
            END,
            updated_at = now()
        WHERE id = %s
        """,
        (
            set_user_id,
            user_id,
            set_notes,
            notes,
            cancelled,
            cancelled,
            shift_id,
        ),
    )
    if cur.rowcount != 1:
        return None
    return fetch_duty_shift(cur, shift_id)


def fetch_shift_coverage_end(cur: psycopg.Cursor) -> Optional[datetime]:
    cur.execute(
        """
        SELECT max(ends_at) AS coverage_end
        FROM duty_shifts
        WHERE cancelled_at IS NULL
        """
    )
    row = cur.fetchone()
    value = row.get("coverage_end") if row else None
    return value if isinstance(value, datetime) else None


__all__ = [
    "create_duty_shift",
    "fetch_active_duty_editors",
    "fetch_duty_schedule",
    "fetch_duty_shift",
    "fetch_duty_shifts",
    "fetch_overlapping_duty_shift",
    "fetch_shift_coverage_end",
    "insert_duty_shifts",
    "update_duty_shift",
    "upsert_duty_schedule",
]
