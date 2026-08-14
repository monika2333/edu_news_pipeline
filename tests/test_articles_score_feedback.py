from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.console import score_feedback_service
from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id="editor-1",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )
    return TestClient(app)


def _feedback_payload() -> dict[str, Any]:
    return {
        "feedback_type": "too_low",
        "score_value": 10,
        "notes": "分数打低了",
        "submitted_by": "editor",
        "submitted_by_user_id": None,
        "updated_at": datetime(2026, 8, 14, tzinfo=timezone.utc),
    }


def test_save_score_feedback_uses_generic_service_with_actor(monkeypatch) -> None:
    calls: dict[str, Any] = {}

    def fake_save(**kwargs: Any) -> dict[str, Any]:
        calls.update(kwargs)
        return _feedback_payload()

    monkeypatch.setattr(score_feedback_service, "save_score_feedback", fake_save)
    client = _client(monkeypatch)

    response = client.put(
        "/api/articles/score-feedback",
        json={
            "article_id": "article-1",
            "feedback_type": "too_low",
            "notes": "分数打低了",
        },
    )

    assert response.status_code == 200
    assert response.json()["score_feedback"]["feedback_type"] == "too_low"
    assert calls["article_id"] == "article-1"
    assert calls["feedback_type"] == "too_low"
    assert calls["actor"].username == "editor"


def test_save_score_feedback_maps_service_errors(monkeypatch) -> None:
    def raise_not_found(**kwargs: Any) -> None:
        raise score_feedback_service.ScoreFeedbackNotFoundError("not found")

    def raise_context(**kwargs: Any) -> None:
        raise score_feedback_service.ScoreFeedbackContextError("no context")

    client = _client(monkeypatch)

    monkeypatch.setattr(score_feedback_service, "save_score_feedback", raise_not_found)
    not_found = client.put(
        "/api/articles/score-feedback",
        json={"article_id": "missing", "feedback_type": "too_high"},
    )
    assert not_found.status_code == 404

    monkeypatch.setattr(score_feedback_service, "save_score_feedback", raise_context)
    context = client.put(
        "/api/articles/score-feedback",
        json={"article_id": "raw-only", "feedback_type": "too_high"},
    )
    assert context.status_code == 409


def test_clear_score_feedback_returns_null(monkeypatch) -> None:
    calls: list[str] = []

    def fake_clear(*, article_id: str) -> bool:
        calls.append(article_id)
        return True

    monkeypatch.setattr(score_feedback_service, "clear_score_feedback", fake_clear)
    client = _client(monkeypatch)

    response = client.post(
        "/api/articles/score-feedback/clear",
        json={"article_id": "article-1"},
    )

    assert response.status_code == 200
    assert response.json() == {"score_feedback": None}
    assert calls == ["article-1"]
