from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

from src.config import get_settings
from src.console.auth_service import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    ConsoleUser,
    resolve_session,
)

_basic_security = HTTPBasic(auto_error=False)
_bearer_security = HTTPBearer(auto_error=False)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _system_user(method: str, *, username: str, display_name: str) -> ConsoleUser:
    return ConsoleUser(
        method=method,
        username=username,
        display_name=display_name,
        role="admin",
    )


def _authentication_headers(*, basic_enabled: bool, token_enabled: bool) -> dict[str, str]:
    if basic_enabled:
        return {"WWW-Authenticate": "Basic"}
    if token_enabled:
        return {"WWW-Authenticate": "Bearer"}
    return {}


async def require_console_user(
    request: Request,
    basic_credentials: Optional[HTTPBasicCredentials] = Depends(_basic_security),
    bearer_credentials: Optional[HTTPAuthorizationCredentials] = Depends(
        _bearer_security
    ),
) -> ConsoleUser:
    raw_session = request.cookies.get(SESSION_COOKIE_NAME) or ""
    if raw_session:
        session_user = resolve_session(raw_session)
        if session_user:
            return session_user

    settings = get_settings()
    basic_user = settings.console_basic_username or ""
    basic_pass = settings.console_basic_password or ""
    token = settings.console_api_token or ""
    basic_enabled = bool(basic_user and basic_pass)
    token_enabled = bool(token)

    if token_enabled and bearer_credentials:
        if secrets.compare_digest(bearer_credentials.credentials or "", token):
            return _system_user(
                "bearer",
                username="api-token",
                display_name="API Token",
            )

    if basic_enabled and basic_credentials:
        username_matches = secrets.compare_digest(
            basic_credentials.username or "",
            basic_user,
        )
        password_matches = secrets.compare_digest(
            basic_credentials.password or "",
            basic_pass,
        )
        if username_matches and password_matches:
            return _system_user(
                "basic",
                username=basic_user,
                display_name=basic_user,
            )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers=_authentication_headers(
            basic_enabled=basic_enabled,
            token_enabled=token_enabled,
        ),
    )


def require_role(*allowed_roles: str) -> Callable[..., ConsoleUser]:
    allowed = frozenset(allowed_roles)
    if not allowed:
        raise ValueError("At least one role is required")

    async def dependency(
        user: ConsoleUser = Depends(require_console_user),
    ) -> ConsoleUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return dependency


def _request_origin(request: Request) -> str:
    default_port = 443 if request.url.scheme == "https" else 80
    port = request.url.port or default_port
    port_suffix = "" if port == default_port else f":{port}"
    return f"{request.url.scheme}://{request.url.hostname}{port_suffix}"


async def require_csrf(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> None:
    if request.method.upper() in _SAFE_METHODS or user.method != "session":
        return

    origin = request.headers.get("origin")
    if not origin or not secrets.compare_digest(origin.rstrip("/"), _request_origin(request)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-origin request rejected",
        )

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME) or ""
    header_token = request.headers.get("x-csrf-token") or ""
    token_hash = hashlib.sha256(header_token.encode("utf-8")).hexdigest()
    if (
        not cookie_token
        or not header_token
        or not user.csrf_token_hash
        or not secrets.compare_digest(cookie_token, header_token)
        or not secrets.compare_digest(user.csrf_token_hash, token_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )


__all__ = [
    "ConsoleUser",
    "require_console_user",
    "require_csrf",
    "require_role",
]
