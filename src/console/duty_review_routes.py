from __future__ import annotations

from datetime import date
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from src.console import duty_review_service, score_feedback_service, shifts_service
from src.console.auth_service import ConsoleUser
from src.console.duty_review_schemas import (
    DutyReviewBatchDecisionRequest,
    DutyReviewBatchEditRequest,
    DutyReviewDuplicateCheckRequest,
    DutyReviewFinalizeRequest,
    DutyReviewOrderRequest,
    DutyReviewRestoreFinalizationRequest,
    DutyReviewUpdateRequest,
    ReportType,
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
from src.console.security import require_role

router = APIRouter(prefix="/api/duty/shifts/{shift_id}", tags=["duty_reviews"])


def _raise_review_error(exc: Exception) -> NoReturn:
    if isinstance(exc, shifts_service.ShiftNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, shifts_service.ShiftPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, duty_review_service.ShiftReviewArticleNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, duty_review_service.ShiftReviewConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _raise_duplicate_review_error(exc: Exception) -> NoReturn:
    if isinstance(exc, DuplicateReviewLimitError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, DuplicateReviewTimeoutError):
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    if isinstance(exc, DuplicateReviewInvalidResponseError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, DuplicateReviewUnavailableError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _raise_review_error(exc)


def _raise_score_feedback_error(exc: ValueError) -> NoReturn:
    if isinstance(exc, score_feedback_service.ScoreFeedbackNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, score_feedback_service.ScoreFeedbackContextError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _raise_review_error(exc)


@router.get("/stats")
def shift_stats(
    shift_id: str,
    report_type: Optional[ReportType] = None,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return duty_review_service.get_stats(
            shift_id=shift_id,
            user=user,
            report_type=report_type,
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.get("/candidates")
def list_candidates(
    shift_id: str,
    limit: int = 30,
    offset: int = 0,
    report_type: Optional[ReportType] = None,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    q: Optional[str] = None,
    published_before: Optional[date] = None,
    hide_submitted: bool = False,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return duty_review_service.list_items(
            shift_id=shift_id,
            user=user,
            decision="pending",
            report_type=report_type,
            limit=limit,
            offset=offset,
            region=region,
            sentiment=sentiment,
            query=q,
            published_before=published_before,
            hide_submitted=hide_submitted,
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.get("/clusters")
def list_clusters(
    shift_id: str,
    report_type: ReportType = "zongbao",
    force_refresh: bool = False,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    include_items: bool = False,
    hide_submitted: bool = False,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return duty_review_service.list_clusters(
            shift_id=shift_id,
            user=user,
            report_type=report_type,
            force_refresh=force_refresh,
            region=region,
            sentiment=sentiment,
            limit=limit,
            offset=offset,
            include_items=include_items,
            hide_submitted=hide_submitted,
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.get("/reviews")
def list_reviews(
    shift_id: str,
    decision: str = "selected",
    limit: int = 30,
    offset: int = 0,
    report_type: Optional[ReportType] = None,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return duty_review_service.list_items(
            shift_id=shift_id,
            user=user,
            decision=decision,
            report_type=report_type,
            limit=limit,
            offset=offset,
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.put("/score-feedback", response_model=ScoreFeedbackResponse)
def save_score_feedback(
    shift_id: str,
    payload: ScoreFeedbackRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> ScoreFeedbackResponse:
    """Create or update score feedback for an article in the owned shift."""
    try:
        feedback = duty_review_service.save_score_feedback(
            shift_id=shift_id,
            article_id=payload.article_id,
            feedback_type=payload.feedback_type,
            notes=payload.notes,
            user=user,
        )
    except (ValueError, PermissionError) as exc:
        _raise_score_feedback_error(exc)
    return ScoreFeedbackResponse(score_feedback=feedback)


@router.post("/score-feedback/clear", response_model=ScoreFeedbackResponse)
def clear_score_feedback(
    shift_id: str,
    payload: ClearScoreFeedbackRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> ScoreFeedbackResponse:
    """Clear score feedback for an article in the owned shift."""
    try:
        duty_review_service.clear_score_feedback(
            shift_id=shift_id,
            article_id=payload.article_id,
            user=user,
        )
    except (ValueError, PermissionError) as exc:
        _raise_score_feedback_error(exc)
    return ScoreFeedbackResponse(score_feedback=None)


@router.post("/duplicate-check")
def check_duplicates(
    shift_id: str,
    payload: DutyReviewDuplicateCheckRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    """Check one review column in the owned shift for duplicate news events."""
    try:
        return duty_review_service.check_duplicates(
            shift_id=shift_id,
            user=user,
            report_type=payload.report_type,
            decision=payload.decision,
        )
    except (
        ValueError,
        PermissionError,
        DuplicateReviewLimitError,
        DuplicateReviewTimeoutError,
        DuplicateReviewInvalidResponseError,
        DuplicateReviewUnavailableError,
    ) as exc:
        _raise_duplicate_review_error(exc)


@router.post("/edit")
def save_edits(
    shift_id: str,
    payload: DutyReviewBatchEditRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    try:
        return duty_review_service.save_edits(
            shift_id=shift_id,
            user=user,
            edits={
                article_id: edit.model_dump(exclude_unset=True)
                for article_id, edit in payload.edits.items()
            },
            versions=payload.versions,
            request_id=request_id,
        )
    except (ValueError, PermissionError, RuntimeError) as exc:
        _raise_review_error(exc)


@router.post("/decide")
def bulk_decide(
    shift_id: str,
    payload: DutyReviewBatchDecisionRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    try:
        return duty_review_service.bulk_decide(
            shift_id=shift_id,
            user=user,
            selected_ids=payload.selected_ids,
            backup_ids=payload.backup_ids,
            discarded_ids=payload.discarded_ids,
            pending_ids=payload.pending_ids,
            versions=payload.versions,
            report_type=payload.report_type,
            request_id=request_id,
        )
    except (ValueError, PermissionError, RuntimeError) as exc:
        _raise_review_error(exc)


@router.put("/reviews/{article_id:path}")
def save_review(
    shift_id: str,
    article_id: str,
    payload: DutyReviewUpdateRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    patch = payload.model_dump(
        exclude={"version"},
        exclude_unset=True,
    )
    try:
        item = duty_review_service.save_review(
            shift_id=shift_id,
            article_id=article_id,
            user=user,
            expected_version=payload.version,
            patch=patch,
            request_id=request_id,
        )
    except (ValueError, PermissionError, RuntimeError) as exc:
        _raise_review_error(exc)
    return {"item": item}


@router.put("/order")
def update_order(
    shift_id: str,
    payload: DutyReviewOrderRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> dict[str, int]:
    try:
        return duty_review_service.update_order(
            shift_id=shift_id,
            user=user,
            selected_order=payload.selected_order,
            backup_order=payload.backup_order,
            request_id=request_id,
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.get("/finalizations")
def get_finalization_status(
    shift_id: str,
    report_type: ReportType = "zongbao",
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    """Return the single current finalization for the shift and report."""
    try:
        return duty_review_service.get_finalization_status(
            shift_id=shift_id,
            user=user,
            report_type=report_type,
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.post("/finalizations")
def finalize_selected_batch(
    shift_id: str,
    payload: DutyReviewFinalizeRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """Finalize and clear the current selected batch for the editor."""
    try:
        return duty_review_service.finalize_selected_batch(
            shift_id=shift_id,
            user=user,
            report_type=payload.report_type,
            request_id=request_id,
        )
    except (ValueError, PermissionError, RuntimeError) as exc:
        _raise_review_error(exc)


@router.post("/finalizations/{batch_id}/restore")
def restore_finalization(
    shift_id: str,
    batch_id: str,
    payload: DutyReviewRestoreFinalizationRequest,
    user: ConsoleUser = Depends(require_role("duty_editor")),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """Restore an entire finalized batch to the selected list."""
    try:
        return duty_review_service.restore_finalized_batch(
            shift_id=shift_id,
            batch_id=batch_id,
            user=user,
            request_id=request_id,
        )
    except (ValueError, PermissionError, RuntimeError) as exc:
        _raise_review_error(exc)


@router.get("/preview")
def preview(
    shift_id: str,
    report_type: ReportType = "zongbao",
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return duty_review_service.build_preview(
            shift_id=shift_id,
            user=user,
            report_type=report_type,
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


__all__ = ["router"]
