from __future__ import annotations

from typing import Any, Literal, NoReturn, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from src.console import admin_summary_service, shifts_service
from src.console.auth_service import ConsoleUser
from src.console.security import require_role

router = APIRouter(prefix="/api/admin", tags=["duty_summary"])


class ImportDutyResultsRequest(BaseModel):
    shift_id: str
    article_ids: list[str] = Field(min_length=1)
    target_status: Literal["selected", "backup"]
    report_type: Literal["zongbao", "wanbao"]


def _raise_summary_error(exc: Exception) -> NoReturn:
    if isinstance(exc, shifts_service.ShiftNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/duty-summary")
def duty_summary(
    limit: int = 60,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    return {"items": admin_summary_service.list_shift_summaries(limit=limit)}


@router.get("/duty-summary/uncovered")
def uncovered_news(
    limit: int = 50,
    offset: int = 0,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    return admin_summary_service.list_uncovered_news(
        limit=limit,
        offset=offset,
    )


@router.get("/duty-summary/{shift_id}/reviews")
def shift_results(
    shift_id: str,
    decision: Optional[str] = None,
    report_type: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    try:
        return admin_summary_service.list_shift_results(
            shift_id=shift_id,
            decision=decision,
            report_type=report_type,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        _raise_summary_error(exc)


@router.post("/duty-summary/import")
def import_results(
    payload: ImportDutyResultsRequest,
    user: ConsoleUser = Depends(require_role("admin")),
    request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    try:
        return admin_summary_service.import_results(
            shift_id=payload.shift_id,
            article_ids=payload.article_ids,
            target_status=payload.target_status,
            report_type=payload.report_type,
            actor=user,
            request_id=request_id,
        )
    except (ValueError, PermissionError) as exc:
        _raise_summary_error(exc)


@router.get("/audit-events")
def audit_events(
    limit: int = 100,
    offset: int = 0,
    actor_user_id: Optional[str] = None,
    target_type: Optional[str] = None,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    return admin_summary_service.list_audit_events(
        limit=limit,
        offset=offset,
        actor_user_id=actor_user_id,
        target_type=target_type,
    )


__all__ = ["router"]
