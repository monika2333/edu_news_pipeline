from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ExportArtifact(BaseModel):
    report_tag: Optional[str] = None
    output_path: Optional[str] = None


class ExportItem(BaseModel):
    id: str
    article_id: Optional[str] = None
    section: Optional[str] = None
    order_index: int
    final_summary: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class LatestExport(BaseModel):
    batch_id: str
    report_date: Optional[date] = None
    sequence_no: int
    generated_by: Optional[str] = None
    export_payload: ExportArtifact
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    item_count: int
    items: List[ExportItem] = Field(default_factory=list)


__all__ = [
    "ExportArtifact",
    "ExportItem",
    "LatestExport",
]
