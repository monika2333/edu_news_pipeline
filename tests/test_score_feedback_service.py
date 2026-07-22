from __future__ import annotations

from typing import Any, Optional

import pytest

from src.adapters import db_postgres_score_feedback
from src.console import score_feedback_service


class FakeAdapter:
    def __init__(self) -> None:
        self.saved: Optional[dict[str, Any]] = None
        self.cleared: Optional[str] = None

    def upsert_score_feedback(
        self,
        article_id: str,
        *,
        feedback_type: str,
        notes: Optional[str],
    ) -> dict[str, Any]:
        self.saved = {
            "article_id": article_id,
            "feedback_type": feedback_type,
            "notes": notes,
        }
        return dict(self.saved)

    def clear_score_feedback(self, article_id: str) -> bool:
        self.cleared = article_id
        return True


def test_save_score_feedback_normalizes_request(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeAdapter()
    monkeypatch.setattr(score_feedback_service, "get_adapter", lambda: adapter)

    result = score_feedback_service.save_score_feedback(
        article_id=" article/1 ",
        feedback_type="TOO_HIGH",
        notes="  理由  ",
    )

    assert result == {
        "article_id": "article/1",
        "feedback_type": "too_high",
        "notes": "理由",
    }


def test_save_score_feedback_rejects_overlong_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(score_feedback_service, "get_adapter", FakeAdapter)

    with pytest.raises(ValueError, match="500"):
        score_feedback_service.save_score_feedback(
            article_id="article-1",
            feedback_type="too_low",
            notes="a" * 501,
        )


def test_save_score_feedback_translates_missing_context(monkeypatch: pytest.MonkeyPatch) -> None:
    class MissingContextAdapter(FakeAdapter):
        def upsert_score_feedback(
            self,
            article_id: str,
            *,
            feedback_type: str,
            notes: Optional[str],
        ) -> dict[str, Any]:
            raise db_postgres_score_feedback.ScoreFeedbackContextMissingError("missing context")

    monkeypatch.setattr(score_feedback_service, "get_adapter", MissingContextAdapter)

    with pytest.raises(score_feedback_service.ScoreFeedbackContextError):
        score_feedback_service.save_score_feedback(
            article_id="article-1",
            feedback_type="too_low",
        )


def test_clear_score_feedback_uses_article_id_with_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = FakeAdapter()
    monkeypatch.setattr(score_feedback_service, "get_adapter", lambda: adapter)

    assert score_feedback_service.clear_score_feedback(article_id="source/item/1") is True
    assert adapter.cleared == "source/item/1"
