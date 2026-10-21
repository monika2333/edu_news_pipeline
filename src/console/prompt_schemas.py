from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from src.domain.prompt import MAX_PROMPT_CONTENT_LENGTH, MAX_PROMPT_NOTE_LENGTH


class PromptUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_PROMPT_CONTENT_LENGTH)
    expected_revision_id: Optional[int] = Field(default=None, ge=1)
    note: Optional[str] = Field(default=None, max_length=MAX_PROMPT_NOTE_LENGTH)


class PromptTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: str = Field(min_length=1, max_length=400)
    content: Optional[str] = Field(default=None, max_length=MAX_PROMPT_CONTENT_LENGTH)


__all__ = ["PromptTestRequest", "PromptUpdateRequest"]
