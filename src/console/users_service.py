from __future__ import annotations

from typing import Any, Optional

from src.adapters.db_postgres_core import get_adapter
from src.console import auth_service
from src.console.auth_service import ConsoleUser


class ConsoleUserNotFoundError(ValueError):
    """Raised when an administrator targets an unknown account."""


def list_users() -> list[dict[str, Any]]:
    return get_adapter().users.fetch_all()


def create_user(
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
    preferred_weekday: Optional[int],
    actor: ConsoleUser,
) -> dict[str, object]:
    return auth_service.create_console_user(
        username=username,
        display_name=display_name,
        password=password,
        role=role,
        preferred_weekday=preferred_weekday,
        actor_user_id=actor.user_id,
    )


def update_user(
    user_id: str,
    *,
    actor: ConsoleUser,
    display_name: Optional[str] = None,
    set_display_name: bool = False,
    role: Optional[str] = None,
    set_role: bool = False,
    preferred_weekday: Optional[int] = None,
    set_preferred_weekday: bool = False,
    is_active: Optional[bool] = None,
    set_is_active: bool = False,
) -> dict[str, Any]:
    if set_display_name and not (display_name or "").strip():
        raise ValueError("Display name must not be blank")
    if set_role and role not in auth_service.VALID_ROLES:
        raise ValueError(f"Invalid console role: {role}")
    if (
        set_preferred_weekday
        and preferred_weekday is not None
        and preferred_weekday not in range(7)
    ):
        raise ValueError("Preferred weekday must be between 0 and 6")
    if not actor.user_id:
        raise PermissionError("A business administrator account is required")
    updated = get_adapter().update_console_user(
        user_id=user_id,
        actor_user_id=actor.user_id,
        display_name=(display_name or "").strip() if set_display_name else None,
        set_display_name=set_display_name,
        role=role,
        set_role=set_role,
        preferred_weekday=preferred_weekday,
        set_preferred_weekday=set_preferred_weekday,
        is_active=is_active,
        set_is_active=set_is_active,
    )
    if not updated:
        raise ConsoleUserNotFoundError("Console user not found")
    return updated


def delete_user(
    user_id: str,
    *,
    actor: ConsoleUser,
) -> None:
    if not actor.user_id:
        raise PermissionError("A business administrator account is required")
    if user_id == actor.user_id:
        raise ValueError("不能删除当前登录账号")
    deleted = get_adapter().delete_console_user(
        user_id=user_id,
        actor_user_id=actor.user_id,
    )
    if not deleted:
        raise ConsoleUserNotFoundError("Console user not found")


def reset_password(
    user_id: str,
    *,
    new_password: str,
    actor: ConsoleUser,
) -> None:
    if not actor.user_id:
        raise PermissionError("A business administrator account is required")
    password_hash = auth_service.hash_password(new_password)
    updated = get_adapter().reset_console_user_password(
        user_id=user_id,
        actor_user_id=actor.user_id,
        password_hash=password_hash,
    )
    if not updated:
        raise ConsoleUserNotFoundError("Console user not found or inactive")


__all__ = [
    "ConsoleUserNotFoundError",
    "create_user",
    "delete_user",
    "list_users",
    "reset_password",
    "update_user",
]
