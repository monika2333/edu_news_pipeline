from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.console.services import xhs_summary

router = APIRouter(prefix="/api/xhs_summary", tags=["xhs_summary"])
logger = logging.getLogger(__name__)


class ExtractRequest(BaseModel):
    raw_text: Optional[str] = None
    source_path: Optional[str] = Field(default=None, description="Optional path to read raw text from.")


class SummarizeRequest(BaseModel):
    links: List[str] = Field(default_factory=list)
    links_path: Optional[str] = None
    summaries_filename: Optional[str] = None


@router.post("/extract")
def extract_links_api(req: ExtractRequest) -> Dict[str, Any]:
    try:
        result = xhs_summary.extract_links(
            raw_text=req.raw_text or "",
            source_path=Path(req.source_path) if req.source_path else None,
        )
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc) or "extract_links is not implemented yet",
        )

    return {"links": result.links}


@router.post("/summarize")
def summarize_api(req: SummarizeRequest) -> Dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="summarize is not implemented yet",
    )


@router.get("/task/{task_id}")
def fetch_task_api(task_id: str) -> Dict[str, Any]:
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"Task {task_id} is not implemented yet",
    )
