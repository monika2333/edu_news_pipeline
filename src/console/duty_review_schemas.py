from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Decision = Literal["pending", "selected", "backup", "discarded"]
ReportType = Literal["zongbao", "wanbao"]


class DutyReviewUpdateRequest(BaseModel):
    version: Optional[int] = Field(default=None, ge=0)
    decision: Optional[Decision] = None
    report_type: Optional[ReportType] = None
    excerpt_text: Optional[str] = Field(default=None, max_length=20_000)
    edited_summary: Optional[str] = Field(default=None, max_length=20_000)
    manual_llm_source: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=5000)


class DutyReviewOrderRequest(BaseModel):
    selected_order: list[str] = Field(default_factory=list)
    backup_order: list[str] = Field(default_factory=list)


__all__ = [
    "Decision",
    "DutyReviewOrderRequest",
    "DutyReviewUpdateRequest",
    "ReportType",
]
