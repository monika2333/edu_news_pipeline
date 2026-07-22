from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import pytest

from src.adapters import db_postgres_score_feedback as score_feedback


class FakeCursor:
    def __init__(self, rows: list[Optional[dict[str, Any]]]) -> None:
        self.rows = list(rows)
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.rowcount = 1

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> Optional[dict[str, Any]]:
        return self.rows.pop(0) if self.rows else None


def _score_row(
    *,
    prompt_key: str = "internal_positive",
    prompt_version: str = "v1",
) -> dict[str, Any]:
    return {
        "external_importance_score": Decimal("82.000"),
        "external_importance_raw": {
            "category": "internal",
            "model_output": "82",
            "prompt_key": prompt_key,
            "prompt_version": prompt_version,
        },
    }


def _feedback_row(feedback_type: str = "too_high") -> dict[str, Any]:
    timestamp = datetime(2026, 7, 22, tzinfo=timezone.utc)
    return {
        "feedback_type": feedback_type,
        "score_value": Decimal("82.000"),
        "prompt_key": "internal_positive",
        "prompt_version": "v1",
        "notes": "分数偏高",
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def test_upsert_score_feedback_uses_current_score_context() -> None:
    cur = FakeCursor([_score_row(), _feedback_row()])

    result = score_feedback.upsert_score_feedback(
        cur,
        "article-1",
        feedback_type="too_high",
        notes="分数偏高",
    )

    assert result["feedback_type"] == "too_high"
    assert "FOR UPDATE" in cur.queries[0]
    assert "ON CONFLICT (article_id, prompt_key, prompt_version)" in cur.queries[1]
    assert cur.params[1][:6] == (
        "article-1",
        "too_high",
        Decimal("82.000"),
        "internal_positive",
        "v1",
        "分数偏高",
    )


def test_upsert_score_feedback_keeps_prompt_keys_independent() -> None:
    cur = FakeCursor(
        [
            _score_row(prompt_key="external_negative", prompt_version="v1"),
            {
                **_feedback_row("too_low"),
                "prompt_key": "external_negative",
            },
        ]
    )

    result = score_feedback.upsert_score_feedback(
        cur,
        "article-1",
        feedback_type="too_low",
        notes=None,
    )

    assert cur.params[1][3:5] == ("external_negative", "v1")
    assert result["prompt_key"] == "external_negative"


def test_clear_score_feedback_targets_only_current_prompt_version() -> None:
    cur = FakeCursor([_score_row(prompt_version="v2")])

    deleted = score_feedback.clear_score_feedback(cur, "article-1")

    assert deleted is True
    assert "DELETE FROM score_feedbacks" in cur.queries[1]
    assert cur.params[1] == ("article-1", "internal_positive", "v2")


def test_upsert_score_feedback_rejects_missing_article() -> None:
    cur = FakeCursor([None])

    with pytest.raises(score_feedback.ScoreFeedbackArticleNotFoundError):
        score_feedback.upsert_score_feedback(
            cur,
            "missing",
            feedback_type="too_high",
            notes=None,
        )


@pytest.mark.parametrize(
    "row",
    [
        {"external_importance_score": 82, "external_importance_raw": None},
        {
            "external_importance_score": 82,
            "external_importance_raw": {"prompt_key": "internal_positive"},
        },
    ],
)
def test_upsert_score_feedback_rejects_incomplete_context(row: dict[str, Any]) -> None:
    cur = FakeCursor([row])

    with pytest.raises(score_feedback.ScoreFeedbackContextMissingError):
        score_feedback.upsert_score_feedback(
            cur,
            "article-1",
            feedback_type="too_high",
            notes=None,
        )
