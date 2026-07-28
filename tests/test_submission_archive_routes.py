from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user


def _admin() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )


def _editor() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )


def _client(user_factory: Callable[[], ConsoleUser]) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = user_factory
    return TestClient(app)


def test_admin_can_parse_report_without_writing_database() -> None:
    response = _client(_admin).post(
        "/api/submission-archive/parse",
        json={
            "pasted_text": (
                "首都教育舆情\n总第1期\n2026年7月28日\n"
                "【舆情速览】\n一、测试条目\n正文（北京日报）"
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["detected_report_type"] == "wanbao"
    assert payload["items"][0]["source"] == "北京日报"


def test_duty_editor_cannot_use_report_import_api() -> None:
    response = _client(_editor).post(
        "/api/submission-archive/parse",
        json={"pasted_text": "任意文本"},
    )

    assert response.status_code == 403
