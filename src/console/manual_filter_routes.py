from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.console import manual_filter_service

router = APIRouter(prefix="/api/manual_filter", tags=["manual_filter"])


class BulkDecideRequest(BaseModel):
    selected_ids: List[str] = Field(default_factory=list)
    backup_ids: List[str] = Field(default_factory=list)
    discarded_ids: List[str] = Field(default_factory=list)
    pending_ids: List[str] = Field(default_factory=list)
    actor: Optional[str] = None
    report_type: str = "zongbao"


class SaveEditsRequest(BaseModel):
    edits: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # article_id -> {"summary": "...", "llm_source": "..."}
    actor: Optional[str] = None
    report_type: str = "zongbao"


class ArchiveRequest(BaseModel):
    article_ids: List[str] = Field(default_factory=list)
    actor: Optional[str] = None
    report_type: str = "zongbao"


class UpdateOrderRequest(BaseModel):
    selected_order: List[str] = Field(default_factory=list)
    backup_order: List[str] = Field(default_factory=list)
    actor: Optional[str] = None
    report_type: str = "zongbao"


@router.get("/candidates")
def list_candidates_api(
    limit: int = 30,
    offset: int = 0,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    cluster: bool = False,
    cluster_threshold: Optional[float] = None,
    force_refresh: bool = False,
    report_type: str = "zongbao",
) -> Dict[str, Any]:
    return manual_filter_service.list_candidates(
        limit=limit,
        offset=offset,
        region=region,
        sentiment=sentiment,
        cluster=cluster,
        cluster_threshold=cluster_threshold,
        force_refresh=force_refresh,
        report_type=report_type,
    )


@router.post("/trigger_clustering")
def trigger_clustering_api() -> Dict[str, Any]:
    return manual_filter_service.trigger_clustering()


@router.post("/decide")
def bulk_decide_api(req: BulkDecideRequest) -> Dict[str, int]:
    return manual_filter_service.bulk_decide(
        selected_ids=req.selected_ids,
        backup_ids=req.backup_ids,
        discarded_ids=req.discarded_ids,
        pending_ids=req.pending_ids,
        actor=req.actor,
        report_type=req.report_type,
    )


@router.get("/review")
def list_review_api(decision: str = "selected", limit: int = 30, offset: int = 0, report_type: str = "zongbao") -> Dict[str, Any]:
    return manual_filter_service.list_review(decision, limit=limit, offset=offset, report_type=report_type)


@router.get("/discarded")
def list_discarded_api(limit: int = 30, offset: int = 0, report_type: str = "zongbao") -> Dict[str, Any]:
    return manual_filter_service.list_discarded(limit=limit, offset=offset, report_type=report_type)


@router.post("/edit")
def save_edits_api(req: SaveEditsRequest) -> Dict[str, int]:
    # The service expects Dict[str, Dict[str, Any]], which matches the pydantic model
    count = manual_filter_service.save_edits(req.edits, actor=req.actor, report_type=req.report_type)
    return {"updated": count}


@router.get("/stats")
def status_counts_api(report_type: str = "zongbao") -> Dict[str, int]:
    return manual_filter_service.status_counts(report_type=report_type)


@router.post("/archive")
def archive_api(req: ArchiveRequest) -> Dict[str, int]:
    count = manual_filter_service.archive_items(
        req.article_ids,
        actor=req.actor,
        report_type=req.report_type,
    )
    return {"exported": count}


@router.post("/order")
def update_order_api(req: UpdateOrderRequest) -> Dict[str, int]:
    return manual_filter_service.update_ranks(
        selected_order=req.selected_order,
        backup_order=req.backup_order,
        actor=req.actor,
        report_type=req.report_type,
    )
