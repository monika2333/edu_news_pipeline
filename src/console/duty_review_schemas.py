from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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


class DutyReviewEditPayload(BaseModel):
    summary: Optional[str] = Field(default=None, max_length=20_000)
    llm_source: Optional[str] = Field(default=None, max_length=500)


class DutyReviewBatchEditRequest(BaseModel):
    edits: dict[str, DutyReviewEditPayload] = Field(default_factory=dict)
    versions: dict[str, int] = Field(default_factory=dict)


class DutyReviewBatchDecisionRequest(BaseModel):
    selected_ids: list[str] = Field(default_factory=list)
    backup_ids: list[str] = Field(default_factory=list)
    discarded_ids: list[str] = Field(default_factory=list)
    pending_ids: list[str] = Field(default_factory=list)
    versions: dict[str, int] = Field(default_factory=dict)
    report_type: ReportType = "zongbao"


class DutyReviewDuplicateCheckRequest(BaseModel):
    report_type: ReportType
    decision: Literal["selected", "backup"]


class DutyReviewFinalizeRequest(BaseModel):
    report_type: ReportType


class DutyReviewRestoreFinalizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DutyReviewOrderRequest(BaseModel):
    selected_order: list[str] = Field(default_factory=list)
    backup_order: list[str] = Field(default_factory=list)


__all__ = [
    "Decision",
    "DutyReviewBatchDecisionRequest",
    "DutyReviewBatchEditRequest",
    "DutyReviewDuplicateCheckRequest",
    "DutyReviewEditPayload",
    "DutyReviewFinalizeRequest",
    "DutyReviewOrderRequest",
    "DutyReviewRestoreFinalizationRequest",
    "DutyReviewUpdateRequest",
    "ReportType",
]
