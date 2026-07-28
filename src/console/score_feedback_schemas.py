from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from src.console import score_feedback_service


class ScoreFeedbackRequest(BaseModel):
    article_id: str = Field(min_length=1)
    feedback_type: Literal["too_high", "too_low"]
    notes: Optional[str] = Field(
        default=None,
        max_length=score_feedback_service.MAX_NOTES_LENGTH,
    )


class ClearScoreFeedbackRequest(BaseModel):
    article_id: str = Field(min_length=1)


class ScoreFeedbackData(BaseModel):
    feedback_type: Literal["too_high", "too_low"]
    score_value: float
    notes: Optional[str] = None
    submitted_by: Optional[str] = None
    submitted_by_user_id: Optional[UUID] = None
    updated_at: datetime


class ScoreFeedbackResponse(BaseModel):
    score_feedback: Optional[ScoreFeedbackData] = None


__all__ = [
    "ClearScoreFeedbackRequest",
    "ScoreFeedbackData",
    "ScoreFeedbackRequest",
    "ScoreFeedbackResponse",
]
