from __future__ import annotations

from typing import Any

from src.adapters.db_postgres_users import (
    fetch_console_users,
    revoke_console_user_sessions,
    soft_delete_console_user,
)


class FakeCursor:
    def __init__(self) -> None:
        self.query = ""
        self.params: tuple[Any, ...] = ()
        self.rowcount = 0

    def execute(
        self,
        query: str,
        params: tuple[Any, ...] = (),
    ) -> None:
        self.query = query
        self.params = params


def test_session_revocation_casts_optional_session_id_to_uuid() -> None:
    cur: Any = FakeCursor()

    revoked = revoke_console_user_sessions(
        cur,
        user_id="user-id",
        except_session_id="session-id",
    )

    assert revoked == 0
    assert "(%s::uuid IS NULL OR id <> %s::uuid)" in cur.query
    assert cur.params == ("user-id", "session-id", "session-id")


def test_user_list_excludes_soft_deleted_accounts() -> None:
    cur: Any = FakeCursor()
    cur.fetchall = lambda: []

    fetch_console_users(cur)

    assert "WHERE deleted_at IS NULL" in cur.query


def test_soft_delete_disables_account_and_records_timestamp() -> None:
    cur: Any = FakeCursor()
    cur.fetchone = lambda: {
        "id": "user-id",
        "is_active": False,
        "deleted_at": "now",
    }

    deleted = soft_delete_console_user(cur, user_id="user-id")

    assert deleted is not None
    assert "SET is_active = false" in cur.query
    assert "deleted_at = now()" in cur.query
    assert cur.params == ("user-id",)
