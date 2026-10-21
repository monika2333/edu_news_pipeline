from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.console import prompt_service
from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user


def _admin() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="测试管理员",
        role="admin",
    )


def _duty() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="duty-id",
        username="duty",
        display_name="值班编辑",
        role="duty_editor",
    )


def _client(user: ConsoleUser = None) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: user or _admin()
    return TestClient(app)


def test_get_prompts_returns_service_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        prompt_service,
        "get_prompts_payload",
        lambda: {"items": [{"key": "external_positive", "version": "v2"}]},
    )

    response = _client().get("/api/admin/prompts")

    assert response.status_code == 200
    assert response.json()["items"][0]["key"] == "external_positive"


def test_get_prompts_requires_admin() -> None:
    response = _client(_duty()).get("/api/admin/prompts")
    assert response.status_code == 403


def test_put_prompt_passes_payload_and_actor(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_save(prompt_key, *, content, expected_revision_id, note, actor):
        captured.update(
            prompt_key=prompt_key,
            content=content,
            expected_revision_id=expected_revision_id,
            note=note,
            actor=actor,
        )
        return {"key": prompt_key, "version": "v3", "content": content}

    monkeypatch.setattr(prompt_service, "save_prompt", fake_save)

    response = _client().put(
        "/api/admin/prompts/external_positive",
        json={
            "content": "new body",
            "expected_revision_id": 12,
            "note": "收紧",
        },
    )

    assert response.status_code == 200
    assert response.json()["item"]["version"] == "v3"
    assert captured["prompt_key"] == "external_positive"
    assert captured["content"] == "new body"
    assert captured["expected_revision_id"] == 12
    assert captured["note"] == "收紧"
    assert captured["actor"].user_id == "admin-id"


def test_put_prompt_conflict_returns_409(monkeypatch) -> None:
    def fake_save(*args, **kwargs):
        raise PromptRevisionConflictError("提示词已被修改")

    monkeypatch.setattr(prompt_service, "save_prompt", fake_save)

    response = _client().put(
        "/api/admin/prompts/external_positive",
        json={"content": "new body", "expected_revision_id": 12},
    )

    assert response.status_code == 409


def test_put_prompt_validation_and_unknown_key(monkeypatch) -> None:
    def fake_save(*args, **kwargs):
        raise ValueError("提示词内容未发生变化")

    monkeypatch.setattr(prompt_service, "save_prompt", fake_save)
    response = _client().put(
        "/api/admin/prompts/external_positive",
        json={"content": "same body", "expected_revision_id": 12},
    )
    assert response.status_code == 422

    def missing(*args, **kwargs):
        raise KeyError("unknown")

    monkeypatch.setattr(prompt_service, "save_prompt", missing)
    response = _client().put(
        "/api/admin/prompts/unknown",
        json={"content": "new body"},
    )
    assert response.status_code == 404


def test_put_prompt_rejects_non_admin(monkeypatch) -> None:
    called = False

    def fake_save(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(prompt_service, "save_prompt", fake_save)
    response = _client(_duty()).put(
        "/api/admin/prompts/external_positive",
        json={"content": "new body", "expected_revision_id": 12},
    )

    assert response.status_code == 403
    assert called is False


def test_prompt_versions_routes(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_list(prompt_key, *, limit):
        captured.update(prompt_key=prompt_key, limit=limit)
        return {"items": [{"id": 1, "version": "v1"}], "has_more": False}

    monkeypatch.setattr(prompt_service, "list_prompt_revisions", fake_list)
    response = _client().get("/api/admin/prompts/external_positive/versions?limit=5")
    assert response.status_code == 200
    assert response.json()["items"][0]["version"] == "v1"
    assert captured == {"prompt_key": "external_positive", "limit": 5}

    monkeypatch.setattr(
        prompt_service,
        "get_prompt_revision",
        lambda prompt_key, revision_id: {"version": "v1", "content": "body"},
    )
    response = _client().get("/api/admin/prompts/external_positive/versions/1")
    assert response.status_code == 200
    assert response.json()["content"] == "body"

    def missing(*args, **kwargs):
        raise KeyError("missing")

    monkeypatch.setattr(prompt_service, "get_prompt_revision", missing)
    response = _client().get("/api/admin/prompts/external_positive/versions/99")
    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"content": ""},
        {"content": "x" * 50_001},
        {"content": "ok", "expected_revision_id": 0},
        {"content": "ok", "note": "x" * 201},
    ],
    ids=["missing", "empty", "too-long", "bad-revision", "long-note"],
)
def test_put_prompt_request_model_rejects_invalid_body(body: dict[str, Any]) -> None:
    response = _client().put("/api/admin/prompts/external_positive", json=body)
    assert response.status_code == 422
