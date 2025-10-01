from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Query

from src.console.schemas.run import PipelineRun, PipelineRunDetail
from src.console.services import runs as runs_service

router = APIRouter(prefix="/runs", tags=["pipeline"])


@router.get("", response_model=List[PipelineRun], summary="List recent pipeline runs")
def list_runs(limit: int = Query(20, ge=1, le=100)) -> List[PipelineRun]:
    return [PipelineRun.model_validate(item) for item in runs_service.list_pipeline_runs(limit)]


@router.get("/{run_id}", response_model=PipelineRunDetail, summary="Fetch run detail")
def get_run(run_id: str) -> PipelineRunDetail:
    result = runs_service.get_pipeline_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return PipelineRunDetail.model_validate(result)


__all__ = ["router"]
