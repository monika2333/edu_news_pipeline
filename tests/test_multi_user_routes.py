from __future__ import annotations

from fastapi.testclient import TestClient

from src.console import (
    articles_service,
    duty_review_service,
    shifts_service,
    users_service,
)
from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user


def _client_for(user: ConsoleUser) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: user
    return TestClient(app)


def test_duty_editor_cannot_call_admin_schedule_api() -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )

    response = _client_for(editor).get("/api/admin/schedules")

    assert response.status_code == 403


def test_duty_editor_cannot_delete_console_user() -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )

    response = _client_for(editor).delete("/api/admin/users/another-user")

    assert response.status_code == 403


def test_duty_editor_cannot_change_admin_duty_discard_state() -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )

    response = _client_for(editor).patch(
        "/api/admin/duty-summary/discard",
        json={
            "shift_id": "shift-1",
            "article_id": "article-1",
            "discarded": True,
        },
    )

    assert response.status_code == 403


def test_admin_can_delete_console_user(monkeypatch) -> None:
    admin = ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )
    captured: dict[str, str] = {}

    def delete_user(user_id: str, *, actor: ConsoleUser) -> None:
        captured["user_id"] = user_id
        captured["actor_user_id"] = actor.user_id or ""

    monkeypatch.setattr(users_service, "delete_user", delete_user)

    response = _client_for(admin).delete("/api/admin/users/editor-id")

    assert response.status_code == 200
    assert response.json()["message"] == "用户已删除，历史记录继续保留"
    assert captured == {
        "user_id": "editor-id",
        "actor_user_id": "admin-id",
    }


def test_duty_editor_cannot_call_admin_manual_filter_api() -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )

    response = _client_for(editor).get("/api/manual_filter/candidates")

    assert response.status_code == 403


def test_duty_editor_can_use_read_only_article_search(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )
    monkeypatch.setattr(
        articles_service,
        "search_articles",
        lambda **kwargs: {
            "items": [],
            "total": 0,
            "limit": kwargs["limit"],
            "page": kwargs["page"],
            "pages": 1,
        },
    )

    response = _client_for(editor).get("/api/articles/search?q=教育")

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_admin_cannot_use_editor_shift_workspace() -> None:
    admin = ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )

    response = _client_for(admin).get("/api/duty/shifts")

    assert response.status_code == 403


def test_editor_can_list_only_service_scoped_shifts(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )
    monkeypatch.setattr(
        shifts_service,
        "list_user_shifts",
        lambda user: [{"id": "shift-id", "user_id": user.user_id}],
    )

    response = _client_for(editor).get("/api/duty/shifts")

    assert response.status_code == 200
    assert response.json()["items"][0]["user_id"] == "editor-id"


def test_stale_review_update_returns_409(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )

    def conflict(**kwargs):
        raise duty_review_service.ShiftReviewConflictError("Review version is stale")

    monkeypatch.setattr(duty_review_service, "save_review", conflict)

    response = _client_for(editor).put(
        "/api/duty/shifts/shift-id/reviews/article-id",
        json={"version": 1, "decision": "selected"},
    )

    assert response.status_code == 409
