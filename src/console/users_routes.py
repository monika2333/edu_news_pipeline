from __future__ import annotations

from typing import Any, Literal, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.console import auth_service, users_service
from src.console.auth_service import ConsoleUser
from src.console.auth_schemas import MessageResponse
from src.console.security import require_role

router = APIRouter(prefix="/api/admin/users", tags=["console_users"])


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10, max_length=1024)
    role: Literal["admin", "duty_editor"]
    preferred_weekday: Optional[int] = Field(default=None, ge=0, le=6)


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=200)
    role: Optional[Literal["admin", "duty_editor"]] = None
    preferred_weekday: Optional[int] = Field(default=None, ge=0, le=6)
    is_active: Optional[bool] = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=10, max_length=1024)


def _raise_user_error(exc: Exception) -> NoReturn:
    if isinstance(exc, users_service.ConsoleUserNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, auth_service.UserAlreadyExistsError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_users(
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    return {"items": users_service.list_users()}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, object]:
    try:
        item = users_service.create_user(
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            role=payload.role,
            preferred_weekday=payload.preferred_weekday,
            actor=user,
        )
    except ValueError as exc:
        _raise_user_error(exc)
    return {"item": item}


@router.patch("/{user_id}")
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    fields = payload.model_fields_set
    try:
        item = users_service.update_user(
            user_id,
            actor=user,
            display_name=payload.display_name,
            set_display_name="display_name" in fields,
            role=payload.role,
            set_role="role" in fields,
            preferred_weekday=payload.preferred_weekday,
            set_preferred_weekday="preferred_weekday" in fields,
            is_active=payload.is_active,
            set_is_active="is_active" in fields,
        )
    except (ValueError, PermissionError) as exc:
        _raise_user_error(exc)
    return {"item": item}


@router.delete("/{user_id}", response_model=MessageResponse)
def delete_user(
    user_id: str,
    user: ConsoleUser = Depends(require_role("admin")),
) -> MessageResponse:
    try:
        users_service.delete_user(user_id, actor=user)
    except (ValueError, PermissionError) as exc:
        _raise_user_error(exc)
    return MessageResponse(message="用户已删除，历史记录继续保留")


@router.post("/{user_id}/reset-password", response_model=MessageResponse)
def reset_password(
    user_id: str,
    payload: ResetPasswordRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> MessageResponse:
    try:
        users_service.reset_password(
            user_id,
            new_password=payload.new_password,
            actor=user,
        )
    except (ValueError, PermissionError) as exc:
        _raise_user_error(exc)
    return MessageResponse(message="密码已重置，原会话已失效")


__all__ = ["router"]
