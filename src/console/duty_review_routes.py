from __future__ import annotations

from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, Header, HTTPException

from src.console import duty_review_service, shifts_service
from src.console.auth_service import ConsoleUser
from src.console.duty_review_schemas import (
    DutyReviewOrderRequest,
    DutyReviewUpdateRequest,
    ReportType,
)
from src.console.security import require_role

router = APIRouter(prefix="/api/duty/shifts/{shift_id}", tags=["duty_reviews"])


def _raise_review_error(exc: Exception) -> NoReturn:
    if isinstance(exc, shifts_service.ShiftNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, shifts_service.ShiftPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, duty_review_service.ShiftReviewConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/stats")
def shift_stats(
    shift_id: str,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return duty_review_service.get_stats(shift_id=shift_id, user=user)
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.get("/candidates")
def list_candidates(
    shift_id: str,
    limit: int = 30,
    offset: int = 0,
    report_type: Optional[ReportType] = None,
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
        )
    except (ValueError, PermissionError) as exc:
        _raise_review_error(exc)


@router.get("/clusters")
def list_clusters(
    shift_id: str,
    report_type: ReportType = "zongbao",
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return duty_review_service.list_clusters(
            shift_id=shift_id,
            user=user,
            report_type=report_type,
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


@router.put("/reviews/{article_id}")
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
