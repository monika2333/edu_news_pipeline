from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class SettingUpdateRequest(BaseModel):
    value: Any
    expected_version: int = Field(ge=1)


class ModelTestRequest(BaseModel):
    step: str
    model: str = Field(min_length=1, max_length=300)
    reasoning: bool


class CrawlAccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    text: str = Field(min_length=1, max_length=4000)


class CrawlAccountPreviewRequest(BaseModel):
    source: str
    text: str = Field(max_length=200000)


class CrawlAccountBulkRequest(BaseModel):
    source: str
    text: str = Field(max_length=200000)


class CrawlAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = None


class CrawlAccountRefreshNamesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(min_length=1, max_length=20)


__all__ = [
    "CrawlAccountBulkRequest",
    "CrawlAccountCreateRequest",
    "CrawlAccountPreviewRequest",
    "CrawlAccountRefreshNamesRequest",
    "CrawlAccountUpdateRequest",
    "ModelTestRequest",
    "SettingUpdateRequest",
]
