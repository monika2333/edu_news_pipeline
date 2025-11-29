from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from src.console.services import manual_filter

router = APIRouter(prefix="/api/manual_filter", tags=["manual_filter"])


class BulkDecideRequest(BaseModel):
    selected_ids: List[str] = []
    backup_ids: List[str] = []
    discarded_ids: List[str] = []
    actor: Optional[str] = None


class SaveEditsRequest(BaseModel):
    edits: Dict[str, Dict[str, Any]]  # article_id -> {"summary": "..."}
    actor: Optional[str] = None


class ExportRequest(BaseModel):
    report_tag: str
    output_path: Optional[str] = None


@router.get("/candidates")
def list_candidates_api(limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    return manual_filter.list_candidates(limit=limit, offset=offset)


@router.post("/decide")
def bulk_decide_api(req: BulkDecideRequest) -> Dict[str, int]:
    return manual_filter.bulk_decide(
        selected_ids=req.selected_ids,
        backup_ids=req.backup_ids,
        discarded_ids=req.discarded_ids,
        actor=req.actor,
    )


@router.get("/review")
def list_review_api(decision: str = "selected", limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    return manual_filter.list_review(decision, limit=limit, offset=offset)


@router.get("/discarded")
def list_discarded_api(limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    return manual_filter.list_discarded(limit=limit, offset=offset)


@router.post("/edit")
def save_edits_api(req: SaveEditsRequest) -> Dict[str, int]:
    # The service expects Dict[str, Dict[str, Any]], which matches the pydantic model
    count = manual_filter.save_edits(req.edits, actor=req.actor)
    return {"updated": count}


@router.get("/stats")
def status_counts_api() -> Dict[str, int]:
    return manual_filter.status_counts()


@router.post("/export")
def export_batch_api(req: ExportRequest) -> Dict[str, Any]:
    # We return the full result including the file path and content if needed
    # Ideally we should return the text content so frontend can display it
    # The service writes to file, but we can read it back or modify service to return text.
    # For now, let's use the existing service and maybe read the file content if needed,
    # or just return the path.
    # The user wants to copy text to clipboard, so we probably need the text content.
    # Let's check `manual_filter.export_batch` implementation again.
    # It returns "output_path". We can read that file.
    
    result = manual_filter.export_batch(
        report_tag=req.report_tag,
        output_path=req.output_path or "outputs/manual_filter_export.txt"
    )
    
    # Read the content to return to frontend
    try:
        with open(result["output_path"], "r", encoding="utf-8") as f:
            content = f.read()
        result["content"] = content
    except Exception:
        result["content"] = ""
        
    return result
