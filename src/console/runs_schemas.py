from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PipelineRunStep(BaseModel):
    order_index: int = Field(..., ge=1)
    step_name: str
    status: str
    started_at: datetime
    finished_at: datetime
    duration_seconds: Optional[float] = None
    error: Optional[str] = None


class PipelineRun(BaseModel):
    run_id: str
    status: str
    trigger_source: Optional[str] = None
    plan: List[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: Optional[datetime] = None
    steps_completed: int = Field(..., ge=0)
    artifacts: Dict[str, Any] = Field(default_factory=dict)
    error_summary: Optional[str] = None


class PipelineRunDetail(PipelineRun):
    steps: List[PipelineRunStep] = Field(default_factory=list)


class PipelineRunTriggerRequest(BaseModel):
    steps: Optional[List[str]] = None
    skip: Optional[List[str]] = None
    continue_on_error: bool = False
    record_metadata: bool = True


__all__ = [
    "PipelineRun",
    "PipelineRunDetail",
    "PipelineRunStep",
    "PipelineRunTriggerRequest",
]
