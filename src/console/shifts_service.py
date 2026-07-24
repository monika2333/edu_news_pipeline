from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from src.adapters.db_postgres_core import get_adapter
from src.config import get_settings
from src.console.auth_service import ConsoleUser

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
SCHEDULE_WEEKDAYS = frozenset(range(7))


class ShiftNotFoundError(ValueError):
    """Raised when a requested shift does not exist."""


class ShiftPermissionError(PermissionError):
    """Raised when a user attempts to access another editor's shift."""


class ShiftScheduleIncompleteError(ValueError):
    """Raised when the seven-day rotation template is incomplete."""


def _require_active_duty_editor(user_id: str) -> dict[str, Any]:
    row = get_adapter().fetch_console_user_by_id(user_id)
    if not row or not bool(row.get("is_active")) or row.get("role") != "duty_editor":
        raise ValueError("Shift assignee must be an active duty editor")
    return row


def _serialize_shift(row: Mapping[str, Any], *, now: Optional[datetime] = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    starts_at = row["starts_at"]
    ends_at = row["ends_at"]
    if row.get("cancelled_at") is not None:
        status_value = "cancelled"
    elif current < starts_at:
        status_value = "upcoming"
    elif current < ends_at:
        status_value = "active"
    else:
        status_value = "ended"
    result = dict(row)
    result["status"] = status_value
    result["coverage_date"] = ends_at.astimezone(BUSINESS_TIMEZONE).date()
    return result


def get_active_duty_editors() -> list[dict[str, Any]]:
    return get_adapter().fetch_active_duty_editors()


def get_schedule() -> list[dict[str, Any]]:
    return get_adapter().fetch_duty_schedule()


def set_schedule(
    assignments: Mapping[int, str],
    *,
    actor: ConsoleUser,
) -> list[dict[str, Any]]:
    if set(assignments) != SCHEDULE_WEEKDAYS:
        raise ShiftScheduleIncompleteError(
            "Duty schedule must assign all seven weekdays"
        )
    for user_id in assignments.values():
        _require_active_duty_editor(user_id)
    return get_adapter().upsert_duty_schedule(
        assignments,
        actor_user_id=actor.user_id,
    )


def _first_coverage_date(now: datetime, boundary_hour: int) -> date:
    local_now = now.astimezone(BUSINESS_TIMEZONE)
    if local_now.hour >= boundary_hour:
        return local_now.date() + timedelta(days=1)
    return local_now.date()


def generate_shifts(
    *,
    days: int = 14,
    actor_user_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if days < 1 or days > 90:
        raise ValueError("days must be between 1 and 90")
    schedule = get_schedule()
    assignments = {int(item["weekday"]): str(item["user_id"]) for item in schedule}
    missing = sorted(SCHEDULE_WEEKDAYS - set(assignments))
    if missing:
        raise ShiftScheduleIncompleteError(
            f"Duty schedule is missing weekdays: {missing}"
        )
    inactive_user_ids: list[str] = []
    for user_id in sorted(set(assignments.values())):
        try:
            _require_active_duty_editor(user_id)
        except ValueError:
            inactive_user_ids.append(user_id)
    if inactive_user_ids:
        raise ShiftScheduleIncompleteError(
            "Duty schedule contains inactive or invalid editors: "
            + ", ".join(inactive_user_ids)
        )
    settings = get_settings()
    boundary_hour = settings.duty_shift_boundary_hour
    current = now or datetime.now(timezone.utc)
    first_date = _first_coverage_date(current, boundary_hour)
    rows: list[dict[str, Any]] = []
    for offset in range(days):
        coverage_date = first_date + timedelta(days=offset)
        ends_at = datetime.combine(
            coverage_date,
            time(hour=boundary_hour),
            tzinfo=BUSINESS_TIMEZONE,
        )
        starts_at = ends_at - timedelta(days=1)
        rows.append(
            {
                "user_id": assignments[coverage_date.weekday()],
                "starts_at": starts_at,
                "ends_at": ends_at,
                "created_by_user_id": actor_user_id,
            }
        )
    inserted = get_adapter().insert_duty_shifts(rows)
    return {
        "requested": len(rows),
        "inserted": inserted,
        "coverage_start": rows[0]["starts_at"],
        "coverage_end": rows[-1]["ends_at"],
    }


def create_shift(
    *,
    user_id: str,
    starts_at: datetime,
    ends_at: datetime,
    notes: Optional[str],
    actor: ConsoleUser,
) -> dict[str, Any]:
    _require_active_duty_editor(user_id)
    if starts_at.tzinfo is None or ends_at.tzinfo is None:
        raise ValueError("Shift timestamps must include a timezone")
    if ends_at <= starts_at:
        raise ValueError("Shift end must be after its start")
    overlap = get_adapter().fetch_overlapping_duty_shift(
        starts_at=starts_at,
        ends_at=ends_at,
    )
    if overlap:
        raise ValueError(f"Shift overlaps existing shift {overlap['id']}")
    created = get_adapter().create_duty_shift(
        user_id=user_id,
        starts_at=starts_at,
        ends_at=ends_at,
        notes=notes,
        actor_user_id=actor.user_id,
    )
    return _serialize_shift(created)


def update_shift(
    shift_id: str,
    *,
    actor: ConsoleUser,
    user_id: Optional[str] = None,
    set_user_id: bool = False,
    notes: Optional[str] = None,
    set_notes: bool = False,
    cancelled: Optional[bool] = None,
) -> dict[str, Any]:
    if set_user_id:
        if not user_id:
            raise ValueError("user_id is required when reassigning a shift")
        _require_active_duty_editor(user_id)
    updated = get_adapter().update_duty_shift(
        shift_id=shift_id,
        actor_user_id=actor.user_id,
        user_id=user_id,
        set_user_id=set_user_id,
        notes=notes,
        set_notes=set_notes,
        cancelled=cancelled,
    )
    if not updated:
        raise ShiftNotFoundError("Duty shift not found")
    return _serialize_shift(updated)


def list_admin_shifts(
    *,
    include_cancelled: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    return [
        _serialize_shift(row)
        for row in get_adapter().fetch_duty_shifts(
            include_cancelled=include_cancelled,
            limit=limit,
        )
    ]


def list_user_shifts(
    user: ConsoleUser,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not user.user_id:
        raise ShiftPermissionError("A business user account is required")
    return [
        _serialize_shift(row)
        for row in get_adapter().fetch_duty_shifts(
            user_id=user.user_id,
            include_cancelled=True,
            limit=limit,
        )
    ]


def require_owned_shift(
    shift_id: str,
    user: ConsoleUser,
    *,
    allow_cancelled: bool = False,
) -> dict[str, Any]:
    row = get_adapter().fetch_duty_shift(shift_id)
    if not row:
        raise ShiftNotFoundError("Duty shift not found")
    if not user.user_id or str(row["user_id"]) != user.user_id:
        raise ShiftPermissionError("Duty shift belongs to another editor")
    if not allow_cancelled and row.get("cancelled_at") is not None:
        raise ShiftPermissionError("Duty shift is cancelled")
    return _serialize_shift(row)


def get_coverage_status(*, now: Optional[datetime] = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    coverage_end = get_adapter().fetch_shift_coverage_end()
    if coverage_end is None:
        return {
            "coverage_end": None,
            "remaining_days": 0,
            "warning": True,
        }
    remaining = max(
        (coverage_end.astimezone(BUSINESS_TIMEZONE).date() - current.astimezone(BUSINESS_TIMEZONE).date()).days,
        0,
    )
    return {
        "coverage_end": coverage_end,
        "remaining_days": remaining,
        "warning": remaining <= 3,
    }


__all__ = [
    "BUSINESS_TIMEZONE",
    "ShiftNotFoundError",
    "ShiftPermissionError",
    "ShiftScheduleIncompleteError",
    "create_shift",
    "generate_shifts",
    "get_active_duty_editors",
    "get_coverage_status",
    "get_schedule",
    "list_admin_shifts",
    "list_user_shifts",
    "require_owned_shift",
    "set_schedule",
    "update_shift",
]
