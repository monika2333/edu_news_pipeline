from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException, Query

from src.console.schemas.run import (
    PipelineRun,
    PipelineRunDetail,
    PipelineRunTriggerRequest,
)
from src.console.services import runs as runs_service

router = APIRouter(prefix="/runs", tags=["pipeline"])


@router.get("", response_model=List[PipelineRun], summary="List recent pipeline runs")
def list_runs(limit: int = Query(20, ge=1, le=100)) -> List[PipelineRun]:
    return [PipelineRun.model_validate(item) for item in runs_service.list_pipeline_runs(limit)]


@router.get("/latest", response_model=PipelineRunDetail, summary="Fetch latest pipeline run")
def latest_run() -> PipelineRunDetail:
    result = runs_service.get_latest_pipeline_run(include_steps=True)
    if result is None:
        raise HTTPException(status_code=404, detail="No pipeline runs recorded yet")
    return PipelineRunDetail.model_validate(result)


@router.get("/{run_id}", response_model=PipelineRunDetail, summary="Fetch run detail")
def get_run(run_id: str) -> PipelineRunDetail:
    result = runs_service.get_pipeline_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return PipelineRunDetail.model_validate(result)


@router.post("/trigger", response_model=PipelineRunDetail, summary="Trigger a pipeline run")
def trigger_run(payload: PipelineRunTriggerRequest) -> PipelineRunDetail:
    try:
        result = runs_service.trigger_pipeline_run(
            steps=payload.steps,
            skip=payload.skip,
            continue_on_error=payload.continue_on_error,
            trigger_source="console-api",
            record_metadata=payload.record_metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PipelineRunDetail.model_validate(result)


__all__ = ["router"]
