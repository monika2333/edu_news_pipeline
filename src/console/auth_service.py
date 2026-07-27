from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Deque, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from psycopg.errors import UniqueViolation

from src.adapters.db_postgres_core import get_adapter
from src.config import get_settings

SESSION_COOKIE_NAME = "console_session"
CSRF_COOKIE_NAME = "console_csrf"
VALID_ROLES = frozenset({"admin", "duty_editor"})

_PASSWORD_HASHER = PasswordHasher()
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 5
_LOGIN_FAILURES: dict[str, Deque[float]] = defaultdict(deque)
_LOGIN_LOCK = threading.Lock()


class AuthenticationError(ValueError):
    """Raised when supplied credentials cannot be authenticated."""


class LoginRateLimitError(ValueError):
    """Raised when a login source has made too many failed attempts."""


class UserAlreadyExistsError(ValueError):
    """Raised when a username already exists."""


@dataclass(frozen=True, slots=True)
class ConsoleUser:
    method: str
    user_id: Optional[str] = None
    username: str = "tester"
    display_name: str = "Test User"
    role: str = "admin"
    session_id: Optional[str] = None
    csrf_token_hash: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class LoginSession:
    user: ConsoleUser
    raw_token: str = field(repr=False)
    csrf_token: str = field(repr=False)
    expires_at: datetime


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _normalize_username(username: str) -> str:
    normalized = username.strip()
    if not normalized:
        raise ValueError("Username must not be blank")
    return normalized


def _validate_password(password: str) -> None:
    if not password:
        raise ValueError("Password must not be empty")


def hash_password(password: str) -> str:
    _validate_password(password)
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def _prune_failures(key: str, now: float) -> Deque[float]:
    failures = _LOGIN_FAILURES[key]
    cutoff = now - _LOGIN_WINDOW_SECONDS
    while failures and failures[0] <= cutoff:
        failures.popleft()
    if not failures:
        _LOGIN_FAILURES.pop(key, None)
        return deque()
    return failures


def ensure_login_allowed(key: str) -> None:
    now = time.monotonic()
    with _LOGIN_LOCK:
        failures = _prune_failures(key, now)
        if len(failures) >= _LOGIN_MAX_FAILURES:
            raise LoginRateLimitError("Too many login attempts; try again later")


def record_login_failure(key: str) -> None:
    now = time.monotonic()
    with _LOGIN_LOCK:
        failures = _prune_failures(key, now)
        if not failures:
            failures = _LOGIN_FAILURES[key]
        failures.append(now)


def clear_login_failures(key: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(key, None)


def create_console_user(
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
    preferred_weekday: Optional[int] = None,
    actor_user_id: Optional[str] = None,
) -> dict[str, object]:
    normalized_username = _normalize_username(username)
    normalized_display_name = display_name.strip()
    if not normalized_display_name:
        raise ValueError("Display name must not be blank")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid console role: {role}")
    if preferred_weekday is not None and preferred_weekday not in range(7):
        raise ValueError("Preferred weekday must be between 0 and 6")
    password_hash = hash_password(password)
    try:
        return get_adapter().create_console_user(
            username=normalized_username,
            display_name=normalized_display_name,
            password_hash=password_hash,
            role=role,
            preferred_weekday=preferred_weekday,
            actor_user_id=actor_user_id,
        )
    except UniqueViolation as exc:
        raise UserAlreadyExistsError(
            f"Console user already exists: {normalized_username}"
        ) from exc


def register_console_user(
    *,
    username: str,
    display_name: str,
    password: str,
    preferred_weekday: int,
) -> dict[str, object]:
    """Create a self-registered editor without assigning any duty shift."""
    if len(username.strip()) < 3:
        raise ValueError("Username must contain at least 3 characters")
    if len(display_name.strip()) < 2:
        raise ValueError("Display name must contain at least 2 characters")
    return create_console_user(
        username=username,
        display_name=display_name,
        password=password,
        role="duty_editor",
        preferred_weekday=preferred_weekday,
        actor_user_id=None,
    )


def authenticate_user(username: str, password: str) -> dict[str, object]:
    normalized_username = _normalize_username(username)
    row = get_adapter().fetch_console_user_by_username(normalized_username)
    password_hash = str(row.get("password_hash") or "") if row else _DUMMY_PASSWORD_HASH
    password_matches = verify_password(password_hash, password)
    if not row or not password_matches or not bool(row.get("is_active")):
        raise AuthenticationError("Invalid username or password")
    return row


def create_login_session(user_row: dict[str, object]) -> LoginSession:
    raw_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.console_session_days
    )
    user_id = str(user_row["id"])
    session_row = get_adapter().create_console_session(
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        csrf_token_hash=_hash_token(csrf_token),
        expires_at=expires_at,
    )
    user = ConsoleUser(
        method="session",
        user_id=user_id,
        username=str(user_row["username"]),
        display_name=str(user_row["display_name"]),
        role=str(user_row["role"]),
        session_id=str(session_row["id"]),
        csrf_token_hash=_hash_token(csrf_token),
    )
    return LoginSession(
        user=user,
        raw_token=raw_token,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


def authenticate_and_create_session(
    *,
    username: str,
    password: str,
) -> LoginSession:
    return create_login_session(authenticate_user(username, password))


def resolve_session(raw_token: str) -> Optional[ConsoleUser]:
    if not raw_token:
        return None
    row = get_adapter().fetch_console_session_by_token_hash(_hash_token(raw_token))
    if not row:
        return None
    session_id = str(row["session_id"])
    get_adapter().touch_console_session(session_id)
    return ConsoleUser(
        method="session",
        user_id=str(row["user_id"]),
        username=str(row["username"]),
        display_name=str(row["display_name"]),
        role=str(row["role"]),
        session_id=session_id,
        csrf_token_hash=str(row["csrf_token_hash"]),
    )


def revoke_session(
    raw_token: str,
    *,
    user: Optional[ConsoleUser] = None,
) -> bool:
    if not raw_token:
        return False
    return get_adapter().revoke_console_session_by_token_hash(
        _hash_token(raw_token),
        actor_user_id=user.user_id if user else None,
    )


def record_login_failure_audit(
    *,
    username: str,
    client_host: str,
    rate_limited: bool,
    request_id: Optional[str] = None,
) -> None:
    normalized_username = username.strip().lower()[:100] or "<blank>"
    get_adapter().record_console_auth_event(
        action=(
            "auth.login.rate_limited"
            if rate_limited
            else "auth.login.failed"
        ),
        target_id=normalized_username,
        actor_user_id=None,
        after_data={
            "client_host": client_host,
            "rate_limited": rate_limited,
        },
        request_id=request_id,
    )


def change_password(
    *,
    user: ConsoleUser,
    current_password: str,
    new_password: str,
) -> None:
    if user.method != "session" or not user.user_id:
        raise AuthenticationError("Password changes require a user session")
    row = get_adapter().fetch_console_user_by_id(user.user_id)
    if not row or not verify_password(
        str(row.get("password_hash") or ""),
        current_password,
    ):
        raise AuthenticationError("Current password is incorrect")
    password_hash = hash_password(new_password)
    updated = get_adapter().change_console_user_password(
        user_id=user.user_id,
        password_hash=password_hash,
        current_session_id=user.session_id,
    )
    if not updated:
        raise AuthenticationError("User account is no longer active")


def cleanup_expired_sessions() -> int:
    return get_adapter().delete_expired_console_sessions()


__all__ = [
    "AuthenticationError",
    "CSRF_COOKIE_NAME",
    "ConsoleUser",
    "LoginRateLimitError",
    "LoginSession",
    "SESSION_COOKIE_NAME",
    "UserAlreadyExistsError",
    "authenticate_and_create_session",
    "authenticate_user",
    "change_password",
    "cleanup_expired_sessions",
    "clear_login_failures",
    "create_console_user",
    "create_login_session",
    "ensure_login_allowed",
    "hash_password",
    "record_login_failure",
    "record_login_failure_audit",
    "register_console_user",
    "resolve_session",
    "revoke_session",
    "verify_password",
]
