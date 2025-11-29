from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.console.services import manual_filter

router = APIRouter(prefix="/api/manual_filter", tags=["manual_filter"])
logger = logging.getLogger(__name__)


class BulkDecideRequest(BaseModel):
    selected_ids: List[str] = Field(default_factory=list)
    backup_ids: List[str] = Field(default_factory=list)
    discarded_ids: List[str] = Field(default_factory=list)
    actor: Optional[str] = None


class SaveEditsRequest(BaseModel):
    edits: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # article_id -> {"summary": "..."}
    actor: Optional[str] = None


class ExportRequest(BaseModel):
    report_tag: str
    template: str = "zongbao"
    period: Optional[int] = None
    total_period: Optional[int] = None
    mark_exported: bool = True
    dry_run: bool = False
    output_path: Optional[str] = None


class UpdateOrderRequest(BaseModel):
    selected_order: List[str] = Field(default_factory=list)
    backup_order: List[str] = Field(default_factory=list)
    actor: Optional[str] = None


@router.get("/candidates")
def list_candidates_api(
    limit: int = 30,
    offset: int = 0,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    cluster: bool = False,
    cluster_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    return manual_filter.list_candidates(
        limit=limit,
        offset=offset,
        region=region,
        sentiment=sentiment,
        cluster=cluster,
        cluster_threshold=cluster_threshold,
    )


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
    result = manual_filter.export_batch(
        report_tag=req.report_tag,
        output_path=req.output_path or "outputs/manual_filter_export.txt",
        template=req.template,
        period=req.period,
        total_period=req.total_period,
        mark_exported=req.mark_exported,
        dry_run=req.dry_run,
    )

    if not result.get("content"):
        try:
            with open(result["output_path"], "r", encoding="utf-8") as f:
                result["content"] = f.read()
        except Exception as exc:  # pragma: no cover - defensive logging for runtime issues
            logger.warning("Failed to read export content from %s: %s", result.get("output_path"), exc)
            result["content"] = ""

    return result


@router.post("/order")
def update_order_api(req: UpdateOrderRequest) -> Dict[str, int]:
    return manual_filter.update_ranks(
        selected_order=req.selected_order,
        backup_order=req.backup_order,
        actor=req.actor,
    )
