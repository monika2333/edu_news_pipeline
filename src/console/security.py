from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBasic, HTTPBasicCredentials, HTTPBearer

from src.config import get_settings

_basic_security = HTTPBasic(auto_error=False)
_bearer_security = HTTPBearer(auto_error=False)


class ConsoleUser:
    """Represents an authenticated console principal."""

    def __init__(self, method: str) -> None:
        self.method = method


async def require_console_user(
    basic_credentials: Optional[HTTPBasicCredentials] = Depends(_basic_security),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_security),
) -> ConsoleUser:
    settings = get_settings()
    basic_user = settings.console_basic_username or ""
    basic_pass = settings.console_basic_password or ""
    token = settings.console_api_token or ""

    basic_enabled = bool(basic_user and basic_pass)
    token_enabled = bool(token)

    if not basic_enabled and not token_enabled:
        return ConsoleUser(method="anonymous")

    if token_enabled and bearer_credentials:
        if secrets.compare_digest(bearer_credentials.credentials or "", token):
            return ConsoleUser(method="bearer")

    if basic_enabled and basic_credentials:
        username_matches = secrets.compare_digest(basic_credentials.username or "", basic_user)
        password_matches = secrets.compare_digest(basic_credentials.password or "", basic_pass)
        if username_matches and password_matches:
            return ConsoleUser(method="basic")

    headers = {}
    if basic_enabled:
        headers["WWW-Authenticate"] = "Basic"
    elif token_enabled:
        headers["WWW-Authenticate"] = "Bearer"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated", headers=headers)


__all__ = ["ConsoleUser", "require_console_user"]
