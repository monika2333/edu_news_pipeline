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
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to extract links")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return {"links": result.links}


@router.post("/summarize")
async def summarize_api(req: SummarizeRequest) -> Dict[str, Any]:
    if req.links_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="links_path 暂不支持，请直接传 links 列表",
        )
    try:
        task_id, prompt = await xhs_summary.start_summary_task(
            req.links,
            summaries_filename=req.summaries_filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to start xhs summarize task")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    return {"task_id": task_id, "prompt": prompt}


@router.get("/task/{task_id}")
def fetch_task_api(task_id: str) -> Dict[str, Any]:
    task = xhs_summary.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )
    return task
