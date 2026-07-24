from __future__ import annotations

from datetime import datetime
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from src.console import shifts_service
from src.console.auth_service import ConsoleUser
from src.console.security import require_role

router = APIRouter(tags=["duty_shifts"])


class ScheduleUpdateRequest(BaseModel):
    assignments: dict[int, str]


class ShiftCreateRequest(BaseModel):
    user_id: str
    starts_at: datetime
    ends_at: datetime
    notes: Optional[str] = Field(default=None, max_length=2000)


class ShiftUpdateRequest(BaseModel):
    user_id: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=2000)
    cancelled: Optional[bool] = None


class GenerateShiftsRequest(BaseModel):
    days: int = Field(default=14, ge=1, le=90)


def _raise_shift_error(exc: Exception) -> NoReturn:
    if isinstance(exc, shifts_service.ShiftNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, shifts_service.ShiftPermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/duty/shifts")
def list_my_shifts(
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> dict[str, Any]:
    try:
        return {"items": shifts_service.list_user_shifts(user)}
    except (ValueError, PermissionError) as exc:
        _raise_shift_error(exc)


@router.get("/api/admin/duty-editors")
def list_duty_editors(
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    return {"items": shifts_service.get_active_duty_editors()}


@router.get("/api/admin/schedules")
def get_schedule(
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    return {
        "items": shifts_service.get_schedule(),
        "coverage": shifts_service.get_coverage_status(),
    }


@router.put("/api/admin/schedules")
def update_schedule(
    payload: ScheduleUpdateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        items = shifts_service.set_schedule(payload.assignments, actor=user)
    except ValueError as exc:
        _raise_shift_error(exc)
    return {"items": items}


@router.get("/api/admin/shifts")
def list_shifts(
    include_cancelled: bool = True,
    limit: int = 100,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    del user
    return {
        "items": shifts_service.list_admin_shifts(
            include_cancelled=include_cancelled,
            limit=limit,
        ),
        "coverage": shifts_service.get_coverage_status(),
    }


@router.post("/api/admin/shifts", status_code=status.HTTP_201_CREATED)
def create_shift(
    payload: ShiftCreateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        item = shifts_service.create_shift(
            user_id=payload.user_id,
            starts_at=payload.starts_at,
            ends_at=payload.ends_at,
            notes=payload.notes,
            actor=user,
        )
    except ValueError as exc:
        _raise_shift_error(exc)
    return {"item": item}


@router.patch("/api/admin/shifts/{shift_id}")
def update_shift(
    shift_id: str,
    payload: ShiftUpdateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    fields = payload.model_fields_set
    try:
        item = shifts_service.update_shift(
            shift_id,
            actor=user,
            user_id=payload.user_id,
            set_user_id="user_id" in fields,
            notes=payload.notes,
            set_notes="notes" in fields,
            cancelled=payload.cancelled if "cancelled" in fields else None,
        )
    except (ValueError, PermissionError) as exc:
        _raise_shift_error(exc)
    return {"item": item}


@router.post("/api/admin/shifts/generate")
def generate_shifts(
    payload: GenerateShiftsRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        return shifts_service.generate_shifts(
            days=payload.days,
            actor_user_id=user.user_id,
        )
    except ValueError as exc:
        _raise_shift_error(exc)


__all__ = ["router"]
