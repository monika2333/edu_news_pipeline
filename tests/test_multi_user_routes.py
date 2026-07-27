from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.console import (
    admin_summary_service,
    articles_service,
    duty_review_service,
    shifts_service,
    users_service,
)
from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.manual_filter_duplicate_service import DuplicateReviewTimeoutError
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


def test_admin_can_bulk_discard_duty_results(monkeypatch) -> None:
    admin = ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )
    captured: dict[str, object] = {}

    def fake_bulk_discard(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"items": [], "discarded": True, "updated": 2}

    monkeypatch.setattr(
        admin_summary_service,
        "set_admin_discarded_many",
        fake_bulk_discard,
    )

    response = _client_for(admin).patch(
        "/api/admin/duty-summary/discard-bulk",
        json={
            "shift_id": "shift-1",
            "article_ids": ["article-1", "article-2"],
            "discarded": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 2
    assert captured["article_ids"] == ["article-1", "article-2"]


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


def test_admin_password_apis_accept_single_character(monkeypatch) -> None:
    admin = ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )
    captured: dict[str, object] = {}

    def create_user(**kwargs: object) -> dict[str, object]:
        captured["created_password"] = kwargs["password"]
        return {"id": "editor-id", **kwargs}

    def reset_password(
        user_id: str,
        *,
        new_password: str,
        actor: ConsoleUser,
    ) -> None:
        captured["reset_user_id"] = user_id
        captured["reset_password"] = new_password
        captured["reset_actor_id"] = actor.user_id

    monkeypatch.setattr(users_service, "create_user", create_user)
    monkeypatch.setattr(users_service, "reset_password", reset_password)
    client = _client_for(admin)

    create_response = client.post(
        "/api/admin/users",
        json={
            "username": "editor",
            "display_name": "Editor",
            "password": "x",
            "role": "duty_editor",
            "preferred_weekday": 0,
        },
    )
    reset_response = client.post(
        "/api/admin/users/editor-id/reset-password",
        json={"new_password": "y"},
    )

    assert create_response.status_code == 201
    assert reset_response.status_code == 200
    assert captured == {
        "created_password": "x",
        "reset_user_id": "editor-id",
        "reset_password": "y",
        "reset_actor_id": "admin-id",
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


def test_batch_review_edit_accepts_article_id_with_slash(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )
    captured: dict[str, Any] = {}
    article_id = "chinanews:/sh/2026/07-27/10666981"

    def save_edits(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"updated": 1, "versions": {article_id: 1}}

    monkeypatch.setattr(duty_review_service, "save_edits", save_edits)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/edit",
        json={
            "edits": {
                article_id: {
                    "summary": "人工摘要",
                    "llm_source": "工人日报",
                }
            },
            "versions": {},
        },
    )

    assert response.status_code == 200
    assert captured["edits"][article_id] == {
        "summary": "人工摘要",
        "llm_source": "工人日报",
    }
    assert response.json()["versions"] == {article_id: 1}


def test_stale_batch_review_decision_returns_409(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )

    def conflict(**kwargs: Any) -> dict[str, Any]:
        raise duty_review_service.ShiftReviewConflictError("Review version is stale")

    monkeypatch.setattr(duty_review_service, "bulk_decide", conflict)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/decide",
        json={
            "selected_ids": ["article-1"],
            "versions": {"article-1": 1},
            "report_type": "zongbao",
        },
    )

    assert response.status_code == 409


def test_editor_can_check_duplicates_in_owned_shift(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )
    captured: dict[str, Any] = {}
    expected = {
        "checked_count": 2,
        "groups": [{"group_id": "duplicate-1", "items": []}],
    }

    def check_duplicates(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(duty_review_service, "check_duplicates", check_duplicates)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/duplicate-check",
        json={"report_type": "wanbao", "decision": "backup"},
    )

    assert response.status_code == 200
    assert response.json() == expected
    assert captured["shift_id"] == "shift-id"
    assert captured["user"].user_id == "editor-id"
    assert captured["report_type"] == "wanbao"
    assert captured["decision"] == "backup"


def test_duty_duplicate_check_timeout_returns_504(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )

    def check_duplicates(**kwargs: Any) -> dict[str, Any]:
        raise DuplicateReviewTimeoutError("AI 查重请求超时，请稍后重试")

    monkeypatch.setattr(duty_review_service, "check_duplicates", check_duplicates)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/duplicate-check",
        json={"report_type": "zongbao", "decision": "selected"},
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "AI 查重请求超时，请稍后重试"


def test_single_review_route_accepts_encoded_slash_id(monkeypatch) -> None:
    editor = ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )
    captured: dict[str, Any] = {}

    def save_review(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "article_id": kwargs["article_id"],
            "version": 1,
        }

    monkeypatch.setattr(duty_review_service, "save_review", save_review)

    response = _client_for(editor).put(
        "/api/duty/shifts/shift-id/reviews/"
        "chinanews%3A%2Fsh%2F2026%2F07-27%2F10666981",
        json={"version": 0, "decision": "selected"},
    )

    assert response.status_code == 200
    assert captured["article_id"] == "chinanews:/sh/2026/07-27/10666981"
