from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SettingUpdateRequest(BaseModel):
    value: Any
    expected_version: int = Field(ge=1)


class ModelTestRequest(BaseModel):
    step: str
    model: str = Field(min_length=1, max_length=300)
    reasoning: bool


class CrawlAccountCreateRequest(BaseModel):
    source: str
    text: str = Field(min_length=1, max_length=4000)
    display_name: Optional[str] = Field(default=None, max_length=200)


class CrawlAccountPreviewRequest(BaseModel):
    source: str
    text: str = Field(max_length=200000)


class CrawlAccountBulkRequest(BaseModel):
    source: str
    text: str = Field(max_length=200000)


class CrawlAccountUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=200)
    enabled: Optional[bool] = None


__all__ = [
    "CrawlAccountBulkRequest",
    "CrawlAccountCreateRequest",
    "CrawlAccountPreviewRequest",
    "CrawlAccountUpdateRequest",
    "ModelTestRequest",
    "SettingUpdateRequest",
]
