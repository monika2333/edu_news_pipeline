from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

SubmissionReportType = Literal["zongbao", "wanbao", "feedback"]


class ParseSubmissionReportRequest(BaseModel):
    pasted_text: str = Field(min_length=1)


class SubmissionItemInput(BaseModel):
    section: Optional[str] = None
    marker: Optional[str] = None
    order_index: int = Field(default=0, ge=0)
    title: str = Field(min_length=1)
    body: str = ""
    source: Optional[str] = None
    urls: list[str] = Field(default_factory=list)


class CreateSubmissionReportRequest(BaseModel):
    report_type: SubmissionReportType
    report_date: date
    compiled_date: date
    issue_no: Optional[str] = None
    title_line: Optional[str] = None
    pasted_text: str = Field(min_length=1)
    items: list[SubmissionItemInput] = Field(min_length=1)
    overwrite: bool = False


class LinkDecisionRequest(BaseModel):
    accepted: bool


__all__ = [
    "CreateSubmissionReportRequest",
    "LinkDecisionRequest",
    "ParseSubmissionReportRequest",
    "SubmissionItemInput",
    "SubmissionReportType",
]
