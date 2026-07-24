from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=10, max_length=1024)


class ConsoleUserResponse(BaseModel):
    user_id: str | None
    username: str
    display_name: str
    role: Literal["admin", "duty_editor"]


class LoginResponse(BaseModel):
    user: ConsoleUserResponse


class MessageResponse(BaseModel):
    message: str


__all__ = [
    "ChangePasswordRequest",
    "ConsoleUserResponse",
    "LoginRequest",
    "LoginResponse",
    "MessageResponse",
]
