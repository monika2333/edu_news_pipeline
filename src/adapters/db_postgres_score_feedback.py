from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional

import psycopg
from psycopg.types.json import Json

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter

VALID_FEEDBACK_TYPES = frozenset({"too_high", "too_low"})
VALID_PROMPT_KEYS = frozenset(
    {
        "external_positive",
        "external_negative",
        "internal_positive",
        "internal_negative",
    }
)


class ScoreFeedbackArticleNotFoundError(ValueError):
    """Raised when a feedback target article does not exist."""


class ScoreFeedbackContextMissingError(ValueError):
    """Raised when the current external score lacks prompt metadata."""


class ScoreFeedbackNamespace:
    """Single-table access to score feedback records."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def upsert(
        self,
        article_id: str,
        *,
        feedback_type: str,
        notes: Optional[str],
        submitted_by: str,
        submitted_by_user_id: Optional[str],
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return upsert_score_feedback(
                cur,
                article_id,
                feedback_type=feedback_type,
                notes=notes,
                submitted_by=submitted_by,
                submitted_by_user_id=submitted_by_user_id,
            )

    def clear(self, article_id: str) -> bool:
        with self._adapter.transaction() as cur:
            return clear_score_feedback(cur, article_id)


def _current_score_context(cur: psycopg.Cursor, article_id: str) -> dict[str, Any]:
    cur.execute(
        """
        SELECT external_importance_score, external_importance_raw
        FROM news_summaries
        WHERE article_id = %s
        FOR UPDATE
        """,
        (article_id,),
    )
    row = cur.fetchone()
    if not row:
        raise ScoreFeedbackArticleNotFoundError(f"Article not found: {article_id}")
    raw_context = row.get("external_importance_raw")
    if not isinstance(raw_context, Mapping):
        raise ScoreFeedbackContextMissingError("External score context is missing")
    score_value = row.get("external_importance_score")
    prompt_key = str(raw_context.get("prompt_key") or "").strip().lower()
    prompt_version = str(raw_context.get("prompt_version") or "").strip()
    if score_value is None or prompt_key not in VALID_PROMPT_KEYS or not prompt_version:
        raise ScoreFeedbackContextMissingError("External score context is incomplete")
    return {
        "score_value": score_value,
        "prompt_key": prompt_key,
        "prompt_version": prompt_version,
        "score_context": dict(raw_context),
    }


def upsert_score_feedback(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    feedback_type: str,
    notes: Optional[str],
    submitted_by: str,
    submitted_by_user_id: Optional[str],
) -> dict[str, Any]:
    if not article_id:
        raise ValueError("upsert_score_feedback requires article_id")
    if feedback_type not in VALID_FEEDBACK_TYPES:
        raise ValueError(f"Invalid feedback type: {feedback_type}")
    context = _current_score_context(cur, article_id)
    cur.execute(
        """
        INSERT INTO score_feedbacks (
            article_id,
            feedback_type,
            score_value,
            prompt_key,
            prompt_version,
            notes,
            score_context,
            submitted_by,
            submitted_by_user_id
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (article_id, prompt_key, prompt_version) DO UPDATE SET
            feedback_type = EXCLUDED.feedback_type,
            score_value = EXCLUDED.score_value,
            notes = EXCLUDED.notes,
            score_context = EXCLUDED.score_context,
            submitted_by = EXCLUDED.submitted_by,
            submitted_by_user_id = EXCLUDED.submitted_by_user_id,
            updated_at = now()
        RETURNING
            feedback_type,
            score_value,
            prompt_key,
            prompt_version,
            notes,
            submitted_by,
            submitted_by_user_id,
            created_at,
            updated_at
        """,
        (
            article_id,
            feedback_type,
            context["score_value"],
            context["prompt_key"],
            context["prompt_version"],
            notes,
            Json(context["score_context"]),
            submitted_by,
            submitted_by_user_id,
        ),
    )
    row = cur.fetchone()
    if not row:
        raise RuntimeError("Score feedback upsert returned no row")
    return dict(row)


def clear_score_feedback(cur: psycopg.Cursor, article_id: str) -> bool:
    if not article_id:
        raise ValueError("clear_score_feedback requires article_id")
    context = _current_score_context(cur, article_id)
    cur.execute(
        """
        DELETE FROM score_feedbacks
        WHERE article_id = %s
          AND prompt_key = %s
          AND prompt_version = %s
        """,
        (
            article_id,
            context["prompt_key"],
            context["prompt_version"],
        ),
    )
    return cur.rowcount > 0


__all__ = [
    "ScoreFeedbackArticleNotFoundError",
    "ScoreFeedbackContextMissingError",
    "ScoreFeedbackNamespace",
    "clear_score_feedback",
    "upsert_score_feedback",
]
