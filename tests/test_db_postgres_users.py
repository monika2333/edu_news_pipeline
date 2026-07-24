from __future__ import annotations

from typing import Any

from src.adapters.db_postgres_users import revoke_console_user_sessions


class FakeCursor:
    def __init__(self) -> None:
        self.query = ""
        self.params: tuple[Any, ...] = ()
        self.rowcount = 0

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
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
