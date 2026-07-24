from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from src.console import auth_service
from src.console.app import create_app
from src.console.auth_service import ConsoleUser, LoginSession
from src.console.security import require_console_user, require_csrf


class FakeAuthAdapter:
    def __init__(self) -> None:
        self.created_session: dict[str, Any] = {}
        self.touched_session_id: Optional[str] = None

    def create_console_session(self, **kwargs: Any) -> dict[str, Any]:
        self.created_session = dict(kwargs)
        return {
            "id": "session-id",
            "user_id": kwargs["user_id"],
            "expires_at": kwargs["expires_at"],
            "created_at": datetime.now(timezone.utc),
        }

    def fetch_console_session_by_token_hash(
        self,
        token_hash: str,
    ) -> dict[str, Any]:
        return {
            "session_id": "session-id",
            "csrf_token_hash": "csrf-hash",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
            "last_seen_at": datetime.now(timezone.utc),
            "user_id": "user-id",
            "username": "admin-a",
            "display_name": "管理员 A",
            "role": "admin",
            "token_hash": token_hash,
        }

    def touch_console_session(self, session_id: str) -> None:
        self.touched_session_id = session_id


def test_password_hash_uses_argon2_and_verifies() -> None:
    password_hash = auth_service.hash_password("a-secure-password")

    assert password_hash.startswith("$argon2")
    assert "a-secure-password" not in password_hash
    assert auth_service.verify_password(password_hash, "a-secure-password")
    assert not auth_service.verify_password(password_hash, "wrong-password")


def test_login_session_persists_only_token_hashes(monkeypatch) -> None:
    adapter = FakeAuthAdapter()
    monkeypatch.setattr(auth_service, "get_adapter", lambda: adapter)

    session = auth_service.create_login_session(
        {
            "id": "user-id",
            "username": "admin-a",
            "display_name": "管理员 A",
            "role": "admin",
        }
    )

    assert adapter.created_session["token_hash"] == hashlib.sha256(
        session.raw_token.encode("utf-8")
    ).hexdigest()
    assert adapter.created_session["csrf_token_hash"] == hashlib.sha256(
        session.csrf_token.encode("utf-8")
    ).hexdigest()
    assert session.raw_token not in adapter.created_session.values()
    assert session.csrf_token not in adapter.created_session.values()


def test_resolve_session_returns_real_business_user(monkeypatch) -> None:
    adapter = FakeAuthAdapter()
    monkeypatch.setattr(auth_service, "get_adapter", lambda: adapter)

    user = auth_service.resolve_session("raw-browser-token")

    assert user == ConsoleUser(
        method="session",
        user_id="user-id",
        username="admin-a",
        display_name="管理员 A",
        role="admin",
        session_id="session-id",
        csrf_token_hash="csrf-hash",
    )
    assert adapter.touched_session_id == "session-id"


def test_login_page_is_public_and_protected_page_redirects() -> None:
    client = TestClient(create_app())

    login_response = client.get("/login")
    register_response = client.get("/register")
    protected_response = client.get("/manual_filter", follow_redirects=False)

    assert login_response.status_code == 200
    assert 'id="login-form"' in login_response.text
    assert register_response.status_code == 200
    assert 'id="register-form"' in register_response.text
    assert "注册不会自动排班" in register_response.text
    assert protected_response.status_code == 303
    assert protected_response.headers["location"].startswith(
        "/login?next=/manual_filter"
    )


def test_self_registration_creates_unassigned_duty_editor(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class RegisterAdapter:
        def create_console_user(self, **kwargs: Any) -> dict[str, object]:
            captured.update(kwargs)
            return {
                "id": "new-user-id",
                "username": kwargs["username"],
                "display_name": kwargs["display_name"],
                "role": kwargs["role"],
                "preferred_weekday": kwargs["preferred_weekday"],
            }

    monkeypatch.setattr(auth_service, "get_adapter", lambda: RegisterAdapter())

    user = auth_service.register_console_user(
        username="zhangming",
        display_name="张明",
        password="a-secure-password",
        preferred_weekday=2,
    )

    assert user["role"] == "duty_editor"
    assert captured["role"] == "duty_editor"
    assert captured["preferred_weekday"] == 2
    assert captured["actor_user_id"] is None


def test_register_api_accepts_name_and_preferred_weekday(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        auth_service,
        "register_console_user",
        lambda **kwargs: captured.update(kwargs) or {"id": "new-user-id"},
    )

    response = TestClient(create_app()).post(
        "/api/auth/register",
        json={
            "username": "zhangming",
            "display_name": "张明",
            "password": "a-secure-password",
            "preferred_weekday": 2,
        },
    )

    assert response.status_code == 201
    assert response.json()["message"] == "注册成功，请登录"
    assert captured == {
        "username": "zhangming",
        "display_name": "张明",
        "password": "a-secure-password",
        "preferred_weekday": 2,
    }


def test_self_registration_validates_trimmed_identity_fields() -> None:
    with pytest.raises(ValueError, match="Username"):
        auth_service.register_console_user(
            username="  a",
            display_name="张明",
            password="a-secure-password",
            preferred_weekday=2,
        )
    with pytest.raises(ValueError, match="Display name"):
        auth_service.register_console_user(
            username="zhangming",
            display_name=" 张",
            password="a-secure-password",
            preferred_weekday=2,
        )


def test_register_api_rejects_duplicate_username(monkeypatch) -> None:
    def reject_registration(**kwargs: Any) -> dict[str, object]:
        del kwargs
        raise auth_service.UserAlreadyExistsError("duplicate")

    monkeypatch.setattr(
        auth_service,
        "register_console_user",
        reject_registration,
    )

    response = TestClient(create_app()).post(
        "/api/auth/register",
        json={
            "username": "zhangming",
            "display_name": "张明",
            "password": "a-secure-password",
            "preferred_weekday": 2,
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "该用户名已被使用"


def test_login_sets_http_only_session_and_readable_csrf_cookie(monkeypatch) -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(days=14)
    session = LoginSession(
        user=ConsoleUser(
            method="session",
            user_id="user-id",
            username="admin-a",
            display_name="管理员 A",
            role="admin",
            session_id="session-id",
            csrf_token_hash="csrf-hash",
        ),
        raw_token="raw-session-token",
        csrf_token="raw-csrf-token",
        expires_at=expires_at,
    )
    monkeypatch.setattr(
        auth_service,
        "authenticate_and_create_session",
        lambda **kwargs: session,
    )

    response = TestClient(create_app()).post(
        "/api/auth/login",
        json={"username": "admin-a", "password": "valid-password"},
    )

    assert response.status_code == 200
    set_cookie = response.headers.get_list("set-cookie")
    assert any(
        "console_session=raw-session-token" in value and "HttpOnly" in value
        for value in set_cookie
    )
    assert any(
        "console_csrf=raw-csrf-token" in value and "HttpOnly" not in value
        for value in set_cookie
    )


def test_failed_login_is_audited_without_password(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def reject_login(**kwargs: Any) -> LoginSession:
        del kwargs
        raise auth_service.AuthenticationError("invalid credentials")

    def capture_audit(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(auth_service, "ensure_login_allowed", lambda key: None)
    monkeypatch.setattr(auth_service, "record_login_failure", lambda key: None)
    monkeypatch.setattr(
        auth_service,
        "authenticate_and_create_session",
        reject_login,
    )
    monkeypatch.setattr(
        auth_service,
        "record_login_failure_audit",
        capture_audit,
    )

    response = TestClient(create_app()).post(
        "/api/auth/login",
        headers={"X-Request-ID": "request-1"},
        json={"username": "Admin-A", "password": "must-not-be-audited"},
    )

    assert response.status_code == 401
    assert captured["username"] == "Admin-A"
    assert captured["rate_limited"] is False
    assert captured["request_id"] == "request-1"
    assert "password" not in captured


def test_session_write_requires_matching_origin_and_csrf_token() -> None:
    csrf_token = "csrf-token"
    user = ConsoleUser(
        method="session",
        user_id="user-id",
        username="admin-a",
        display_name="管理员 A",
        role="admin",
        session_id="session-id",
        csrf_token_hash=hashlib.sha256(csrf_token.encode("utf-8")).hexdigest(),
    )
    app = FastAPI()

    @app.post("/mutate", dependencies=[Depends(require_csrf)])
    def mutate() -> dict[str, bool]:
        return {"ok": True}

    app.dependency_overrides[require_console_user] = lambda: user
    client = TestClient(app)
    client.cookies.set(auth_service.CSRF_COOKIE_NAME, csrf_token)

    accepted = client.post(
        "/mutate",
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf_token,
        },
    )
    rejected = client.post(
        "/mutate",
        headers={
            "Origin": "https://attacker.example",
            "X-CSRF-Token": csrf_token,
        },
    )

    assert accepted.status_code == 200
    assert rejected.status_code == 403
