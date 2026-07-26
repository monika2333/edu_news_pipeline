from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import psycopg


def create_console_user(
    cur: psycopg.Cursor,
    *,
    username: str,
    display_name: str,
    password_hash: str,
    role: str,
    preferred_weekday: Optional[int] = None,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO console_users (
            username,
            display_name,
            password_hash,
            role,
            preferred_weekday,
            password_changed_at
        )
        VALUES (%s, %s, %s, %s, %s, now())
        RETURNING
            id,
            username,
            display_name,
            role,
            preferred_weekday,
            is_active,
            password_changed_at,
            last_login_at,
            created_at,
            updated_at,
            deleted_at
        """,
        (username, display_name, password_hash, role, preferred_weekday),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to create console user")
    return dict(row)


def fetch_console_user_by_username(
    cur: psycopg.Cursor,
    username: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id,
            username,
            display_name,
            password_hash,
            role,
            preferred_weekday,
            is_active,
            password_changed_at,
            last_login_at,
            created_at,
            updated_at,
            deleted_at
        FROM console_users
        WHERE lower(username) = lower(%s)
          AND deleted_at IS NULL
        """,
        (username,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_console_user_by_id(
    cur: psycopg.Cursor,
    user_id: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id,
            username,
            display_name,
            password_hash,
            role,
            preferred_weekday,
            is_active,
            password_changed_at,
            last_login_at,
            created_at,
            updated_at,
            deleted_at
        FROM console_users
        WHERE id = %s
          AND deleted_at IS NULL
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_console_users(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id,
            username,
            display_name,
            role,
            preferred_weekday,
            is_active,
            password_changed_at,
            last_login_at,
            created_at,
            updated_at,
            deleted_at
        FROM console_users
        WHERE deleted_at IS NULL
        ORDER BY
            CASE role WHEN 'admin' THEN 0 ELSE 1 END,
            display_name,
            username
        """
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_console_user_for_update(
    cur: psycopg.Cursor,
    user_id: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            id,
            username,
            display_name,
            password_hash,
            role,
            preferred_weekday,
            is_active,
            password_changed_at,
            last_login_at,
            created_at,
            updated_at,
            deleted_at
        FROM console_users
        WHERE id = %s
          AND deleted_at IS NULL
        FOR UPDATE
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def lock_active_admin_ids(cur: psycopg.Cursor) -> list[str]:
    cur.execute(
        """
        SELECT id
        FROM console_users
        WHERE role = 'admin'
          AND is_active = true
          AND deleted_at IS NULL
        FOR UPDATE
        """
    )
    return [str(row["id"]) for row in cur.fetchall()]


def fetch_future_shifts_for_user(
    cur: psycopg.Cursor,
    user_id: str,
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, starts_at, ends_at
        FROM duty_shifts
        WHERE user_id = %s
          AND cancelled_at IS NULL
          AND ends_at > now()
        ORDER BY starts_at
        """,
        (user_id,),
    )
    return [dict(row) for row in cur.fetchall()]


def update_console_user(
    cur: psycopg.Cursor,
    *,
    user_id: str,
    display_name: Optional[str] = None,
    set_display_name: bool = False,
    role: Optional[str] = None,
    set_role: bool = False,
    preferred_weekday: Optional[int] = None,
    set_preferred_weekday: bool = False,
    is_active: Optional[bool] = None,
    set_is_active: bool = False,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        UPDATE console_users
        SET display_name = CASE WHEN %s THEN %s ELSE display_name END,
            role = CASE WHEN %s THEN %s ELSE role END,
            preferred_weekday = CASE
                WHEN %s THEN %s
                ELSE preferred_weekday
            END,
            is_active = CASE WHEN %s THEN %s ELSE is_active END,
            updated_at = now()
        WHERE id = %s
          AND deleted_at IS NULL
        RETURNING
            id,
            username,
            display_name,
            role,
            preferred_weekday,
            is_active,
            password_changed_at,
            last_login_at,
            created_at,
            updated_at,
            deleted_at
        """,
        (
            set_display_name,
            display_name,
            set_role,
            role,
            set_preferred_weekday,
            preferred_weekday,
            set_is_active,
            is_active,
            user_id,
        ),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def record_console_user_login(cur: psycopg.Cursor, user_id: str) -> None:
    cur.execute(
        """
        UPDATE console_users
        SET last_login_at = now(),
            updated_at = now()
        WHERE id = %s
        """,
        (user_id,),
    )


def update_console_user_password(
    cur: psycopg.Cursor,
    *,
    user_id: str,
    password_hash: str,
) -> bool:
    cur.execute(
        """
        UPDATE console_users
        SET password_hash = %s,
            password_changed_at = now(),
            updated_at = now()
        WHERE id = %s
          AND is_active = true
          AND deleted_at IS NULL
        """,
        (password_hash, user_id),
    )
    return cur.rowcount == 1


def create_console_session(
    cur: psycopg.Cursor,
    *,
    user_id: str,
    token_hash: str,
    csrf_token_hash: str,
    expires_at: datetime,
) -> dict[str, Any]:
    cur.execute(
        """
        INSERT INTO console_user_sessions (
            user_id,
            token_hash,
            csrf_token_hash,
            expires_at,
            last_seen_at
        )
        VALUES (%s, %s, %s, %s, now())
        RETURNING id, user_id, expires_at, created_at
        """,
        (user_id, token_hash, csrf_token_hash, expires_at),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Failed to create console session")
    return dict(row)


def fetch_console_session_by_token_hash(
    cur: psycopg.Cursor,
    token_hash: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            s.id AS session_id,
            s.csrf_token_hash,
            s.expires_at,
            s.last_seen_at,
            u.id AS user_id,
            u.username,
            u.display_name,
            u.role
        FROM console_user_sessions s
        JOIN console_users u ON u.id = s.user_id
        WHERE s.token_hash = %s
          AND s.revoked_at IS NULL
          AND s.expires_at > now()
          AND u.is_active = true
          AND u.deleted_at IS NULL
        """,
        (token_hash,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def touch_console_session(cur: psycopg.Cursor, session_id: str) -> None:
    cur.execute(
        """
        UPDATE console_user_sessions
        SET last_seen_at = now()
        WHERE id = %s
          AND (
              last_seen_at IS NULL
              OR last_seen_at < now() - interval '5 minutes'
          )
        """,
        (session_id,),
    )


def revoke_console_session_by_token_hash(
    cur: psycopg.Cursor,
    token_hash: str,
) -> bool:
    cur.execute(
        """
        UPDATE console_user_sessions
        SET revoked_at = now()
        WHERE token_hash = %s
          AND revoked_at IS NULL
        """,
        (token_hash,),
    )
    return cur.rowcount > 0


def revoke_console_user_sessions(
    cur: psycopg.Cursor,
    *,
    user_id: str,
    except_session_id: Optional[str] = None,
) -> int:
    cur.execute(
        """
        UPDATE console_user_sessions
        SET revoked_at = now()
        WHERE user_id = %s
          AND revoked_at IS NULL
          AND (%s::uuid IS NULL OR id <> %s::uuid)
        """,
        (user_id, except_session_id, except_session_id),
    )
    return cur.rowcount


def delete_duty_schedules_for_user(
    cur: psycopg.Cursor,
    *,
    user_id: str,
) -> int:
    cur.execute(
        """
        DELETE FROM duty_schedules
        WHERE user_id = %s
        """,
        (user_id,),
    )
    return cur.rowcount


def soft_delete_console_user(
    cur: psycopg.Cursor,
    *,
    user_id: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        UPDATE console_users
        SET is_active = false,
            deleted_at = now(),
            updated_at = now()
        WHERE id = %s
          AND deleted_at IS NULL
        RETURNING
            id,
            username,
            display_name,
            role,
            preferred_weekday,
            is_active,
            password_changed_at,
            last_login_at,
            created_at,
            updated_at,
            deleted_at
        """,
        (user_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def delete_expired_console_sessions(cur: psycopg.Cursor) -> int:
    cur.execute(
        """
        DELETE FROM console_user_sessions
        WHERE expires_at <= now()
           OR revoked_at < now() - interval '30 days'
        """
    )
    return cur.rowcount


__all__ = [
    "create_console_session",
    "create_console_user",
    "delete_duty_schedules_for_user",
    "delete_expired_console_sessions",
    "fetch_console_session_by_token_hash",
    "fetch_console_user_by_id",
    "fetch_console_user_for_update",
    "fetch_console_user_by_username",
    "fetch_console_users",
    "fetch_future_shifts_for_user",
    "lock_active_admin_ids",
    "record_console_user_login",
    "revoke_console_session_by_token_hash",
    "revoke_console_user_sessions",
    "soft_delete_console_user",
    "touch_console_session",
    "update_console_user",
    "update_console_user_password",
]
