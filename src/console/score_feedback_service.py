from __future__ import annotations

from typing import Any, Optional

from src.adapters import db_postgres_score_feedback
from src.adapters.db_postgres_core import get_adapter
from src.console.auth_service import ConsoleUser

MAX_NOTES_LENGTH = 500
VALID_FEEDBACK_TYPES = frozenset({"too_high", "too_low"})


class ScoreFeedbackNotFoundError(ValueError):
    """Raised when the requested article does not exist."""


class ScoreFeedbackContextError(ValueError):
    """Raised when an article has no current versioned score context."""


def _normalize_article_id(article_id: str) -> str:
    normalized = str(article_id or "").strip()
    if not normalized:
        raise ValueError("article_id is required")
    return normalized


def _normalize_feedback_type(feedback_type: str) -> str:
    normalized = str(feedback_type or "").strip().lower()
    if normalized not in VALID_FEEDBACK_TYPES:
        raise ValueError("feedback_type must be too_high or too_low")
    return normalized


def _normalize_notes(notes: Optional[str]) -> Optional[str]:
    if notes is None:
        return None
    normalized = str(notes).strip()
    if len(normalized) > MAX_NOTES_LENGTH:
        raise ValueError(f"notes must not exceed {MAX_NOTES_LENGTH} characters")
    return normalized or None


def _translate_adapter_error(exc: ValueError) -> None:
    if isinstance(exc, db_postgres_score_feedback.ScoreFeedbackArticleNotFoundError):
        raise ScoreFeedbackNotFoundError(str(exc)) from exc
    if isinstance(exc, db_postgres_score_feedback.ScoreFeedbackContextMissingError):
        raise ScoreFeedbackContextError(str(exc)) from exc
    raise exc


def save_score_feedback(
    *,
    article_id: str,
    feedback_type: str,
    notes: Optional[str] = None,
    actor: ConsoleUser,
) -> dict[str, Any]:
    normalized_article_id = _normalize_article_id(article_id)
    normalized_feedback_type = _normalize_feedback_type(feedback_type)
    normalized_notes = _normalize_notes(notes)
    adapter = get_adapter()
    try:
        return adapter.upsert_score_feedback(
            normalized_article_id,
            feedback_type=normalized_feedback_type,
            notes=normalized_notes,
            submitted_by=actor.username,
            submitted_by_user_id=actor.user_id,
        )
    except ValueError as exc:
        _translate_adapter_error(exc)
        raise


def clear_score_feedback(*, article_id: str) -> bool:
    normalized_article_id = _normalize_article_id(article_id)
    adapter = get_adapter()
    try:
        return adapter.clear_score_feedback(normalized_article_id)
    except ValueError as exc:
        _translate_adapter_error(exc)
        raise


__all__ = [
    "MAX_NOTES_LENGTH",
    "ScoreFeedbackContextError",
    "ScoreFeedbackNotFoundError",
    "clear_score_feedback",
    "save_score_feedback",
]
