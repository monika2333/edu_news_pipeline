from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Literal, NoReturn, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.console import (
    manual_filter_admin_service,
    manual_filter_service,
    score_feedback_service,
)
from src.console.manual_filter_duplicate_service import (
    DuplicateReviewInvalidResponseError,
    DuplicateReviewLimitError,
    DuplicateReviewTimeoutError,
    DuplicateReviewUnavailableError,
)
from src.console.score_feedback_schemas import (
    ClearScoreFeedbackRequest,
    ScoreFeedbackRequest,
    ScoreFeedbackResponse,
)
from src.console.security import ConsoleUser, require_console_user
from src.domain.report_type import NewsReportType

router = APIRouter(prefix="/api/manual_filter", tags=["manual_filter"])


class BulkDecideRequest(BaseModel):
    selected_ids: List[str] = Field(default_factory=list)
    backup_ids: List[str] = Field(default_factory=list)
    discarded_ids: List[str] = Field(default_factory=list)
    pending_ids: List[str] = Field(default_factory=list)
    versions: Dict[str, int] = Field(default_factory=dict)
    report_type: str = "zongbao"


class SaveEditsRequest(BaseModel):
    edits: Dict[str, Dict[str, Any]] = Field(default_factory=dict)  # article_id -> {"summary": "...", "llm_source": "..."}
    versions: Dict[str, int] = Field(default_factory=dict)
    report_type: str = "zongbao"


class ArchiveRequest(BaseModel):
    article_ids: List[str] = Field(default_factory=list)
    versions: Dict[str, int] = Field(default_factory=dict)
    report_type: str = "zongbao"


ReviewGroupKey = Literal[
    "internal_positive",
    "internal_negative",
    "external_positive",
    "external_negative",
]


class UpdateOrderRequest(BaseModel):
    selected_order: List[str] = Field(default_factory=list)
    backup_order: List[str] = Field(default_factory=list)
    group_orders: Dict[ReviewGroupKey, List[str]] = Field(default_factory=dict)
    report_type: str = "zongbao"


class BulkDiscardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region: Literal["internal", "external"]
    sentiment: Literal["positive", "negative"]
    q: Optional[str] = None
    created_before: Optional[date] = None
    dry_run: bool = True


class DuplicateCheckRequest(BaseModel):
    report_type: NewsReportType
    decision: Literal["selected", "backup"]


def _raise_score_feedback_http_error(exc: ValueError) -> NoReturn:
    if isinstance(exc, score_feedback_service.ScoreFeedbackNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, score_feedback_service.ScoreFeedbackContextError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _raise_manual_write_http_error(exc: Exception) -> NoReturn:
    if isinstance(exc, manual_filter_admin_service.ManualReviewConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/candidates")
def list_candidates_api(
    limit: int = 30,
    offset: int = 0,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    cluster: bool = False,
    cluster_threshold: Optional[float] = None,
    force_refresh: bool = False,
    q: Optional[str] = None,
    created_before: Optional[date] = None,
    view_mode: Optional[str] = None,
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
        q=q,
        created_before=created_before,
        view_mode=view_mode,
        report_type=report_type,
    )


@router.post("/trigger_clustering")
def trigger_clustering_api() -> Dict[str, Any]:
    return manual_filter_service.trigger_clustering()


@router.put("/score-feedback", response_model=ScoreFeedbackResponse)
def save_score_feedback_api(
    req: ScoreFeedbackRequest,
    user: ConsoleUser = Depends(require_console_user),
) -> ScoreFeedbackResponse:
    """Create or update feedback for the article's current external score."""
    try:
        feedback = score_feedback_service.save_score_feedback(
            article_id=req.article_id,
            feedback_type=req.feedback_type,
            notes=req.notes,
            actor=user,
        )
    except ValueError as exc:
        _raise_score_feedback_http_error(exc)
    return ScoreFeedbackResponse(score_feedback=feedback)


@router.post("/score-feedback/clear", response_model=ScoreFeedbackResponse)
def clear_score_feedback_api(req: ClearScoreFeedbackRequest) -> ScoreFeedbackResponse:
    """Clear feedback for the article's current external score."""
    try:
        score_feedback_service.clear_score_feedback(article_id=req.article_id)
    except ValueError as exc:
        _raise_score_feedback_http_error(exc)
    return ScoreFeedbackResponse(score_feedback=None)


@router.post("/decide")
def bulk_decide_api(
    req: BulkDecideRequest,
    user: ConsoleUser = Depends(require_console_user),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> Dict[str, Any]:
    try:
        return manual_filter_admin_service.bulk_decide(
            selected_ids=req.selected_ids,
            backup_ids=req.backup_ids,
            discarded_ids=req.discarded_ids,
            pending_ids=req.pending_ids,
            versions=req.versions,
            actor=user,
            report_type=req.report_type,
            request_id=request_id,
        )
    except (ValueError, RuntimeError) as exc:
        _raise_manual_write_http_error(exc)


@router.get("/review")
def list_review_api(decision: str = "selected", limit: int = 30, offset: int = 0, report_type: str = "zongbao") -> Dict[str, Any]:
    return manual_filter_service.list_review(decision, limit=limit, offset=offset, report_type=report_type)


@router.post("/duplicate-check")
def duplicate_check_api(req: DuplicateCheckRequest) -> Dict[str, Any]:
    """Check the active review column for duplicate news events."""
    try:
        return manual_filter_service.check_duplicates(
            report_type=req.report_type,
            decision=req.decision,
        )
    except DuplicateReviewLimitError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateReviewTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except DuplicateReviewInvalidResponseError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except DuplicateReviewUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/discarded")
def list_discarded_api(limit: int = 30, offset: int = 0, report_type: str = "zongbao") -> Dict[str, Any]:
    return manual_filter_service.list_discarded(limit=limit, offset=offset, report_type=report_type)


@router.post("/edit")
def save_edits_api(
    req: SaveEditsRequest,
    user: ConsoleUser = Depends(require_console_user),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> Dict[str, Any]:
    try:
        return manual_filter_admin_service.save_edits(
            req.edits,
            versions=req.versions,
            actor=user,
            report_type=req.report_type,
            request_id=request_id,
        )
    except (ValueError, RuntimeError) as exc:
        _raise_manual_write_http_error(exc)


@router.get("/stats")
def status_counts_api(report_type: str = "zongbao") -> Dict[str, int]:
    return manual_filter_service.status_counts(report_type=report_type)


@router.post("/archive")
def archive_api(
    req: ArchiveRequest,
    user: ConsoleUser = Depends(require_console_user),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> Dict[str, Any]:
    try:
        return manual_filter_admin_service.archive_items(
            req.article_ids,
            versions=req.versions,
            actor=user,
            report_type=req.report_type,
            request_id=request_id,
        )
    except (ValueError, RuntimeError) as exc:
        _raise_manual_write_http_error(exc)


@router.post("/order")
def update_order_api(
    req: UpdateOrderRequest,
    user: ConsoleUser = Depends(require_console_user),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> Dict[str, int]:
    try:
        return manual_filter_admin_service.update_ranks(
            selected_order=req.selected_order,
            backup_order=req.backup_order,
            group_orders=req.group_orders,
            actor=user,
            report_type=req.report_type,
            request_id=request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/bulk-discard")
def bulk_discard_api(
    req: BulkDiscardRequest,
    user: ConsoleUser = Depends(require_console_user),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> Dict[str, int]:
    try:
        return manual_filter_admin_service.bulk_discard_candidates(
            region=req.region,
            sentiment=req.sentiment,
            query=req.q,
            created_before=req.created_before,
            dry_run=req.dry_run,
            actor=user,
            request_id=request_id,
        )
    except (ValueError, RuntimeError) as exc:
        _raise_manual_write_http_error(exc)
