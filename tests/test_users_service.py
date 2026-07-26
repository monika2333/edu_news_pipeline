from __future__ import annotations

from typing import Any, Optional

import pytest

from src.console import users_service
from src.console.auth_service import ConsoleUser


class FakeUsersAdapter:
    def __init__(self, deleted: Optional[dict[str, Any]] = None) -> None:
        self.deleted = deleted
        self.delete_query: dict[str, str] = {}

    def delete_console_user(
        self,
        *,
        user_id: str,
        actor_user_id: str,
    ) -> Optional[dict[str, Any]]:
        self.delete_query = {
            "user_id": user_id,
            "actor_user_id": actor_user_id,
        }
        return self.deleted


def _admin() -> ConsoleUser:
    return ConsoleUser(
        method="session",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )


def test_delete_user_rejects_current_account(monkeypatch) -> None:
    adapter = FakeUsersAdapter()
    monkeypatch.setattr(users_service, "get_adapter", lambda: adapter)

    with pytest.raises(ValueError, match="不能删除当前登录账号"):
        users_service.delete_user("admin-id", actor=_admin())

    assert adapter.delete_query == {}


def test_delete_user_uses_authenticated_admin_identity(monkeypatch) -> None:
    adapter = FakeUsersAdapter(deleted={"id": "editor-id"})
    monkeypatch.setattr(users_service, "get_adapter", lambda: adapter)

    users_service.delete_user("editor-id", actor=_admin())

    assert adapter.delete_query == {
        "user_id": "editor-id",
        "actor_user_id": "admin-id",
    }


def test_delete_user_rejects_missing_account(monkeypatch) -> None:
    adapter = FakeUsersAdapter()
    monkeypatch.setattr(users_service, "get_adapter", lambda: adapter)

    with pytest.raises(users_service.ConsoleUserNotFoundError):
        users_service.delete_user("missing-id", actor=_admin())
