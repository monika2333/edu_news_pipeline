from __future__ import annotations

from fastapi.testclient import TestClient

from src.console import duty_review_service, shifts_service
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
