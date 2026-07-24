from __future__ import annotations

from typing import Any, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_shift_reviews import (
    VALID_DECISIONS,
    VALID_REPORT_TYPES,
)
from src.console import duty_review_service
from src.console.auth_service import ConsoleUser
from src.console.shifts_service import ShiftNotFoundError


def list_shift_summaries(*, limit: int = 60) -> list[dict[str, Any]]:
    rows = get_adapter().fetch_admin_shift_summaries(limit=limit)
    return [
        {
            **row,
            "total": int(row.get("total") or 0),
            "pending": int(row.get("pending") or 0),
            "selected": int(row.get("selected") or 0),
            "backup": int(row.get("backup") or 0),
            "discarded": int(row.get("discarded") or 0),
        }
        for row in rows
    ]


def list_shift_results(
    *,
    shift_id: str,
    decision: Optional[str],
    report_type: Optional[str],
    mismatch_only: bool,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    if not get_adapter().fetch_duty_shift(shift_id):
        raise ShiftNotFoundError("Duty shift not found")
    if decision and decision not in VALID_DECISIONS:
        raise ValueError(f"Invalid review decision: {decision}")
    if report_type and report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"Invalid report type: {report_type}")
    rows, total = get_adapter().fetch_shift_review_items(
        shift_id=shift_id,
        decision=decision,
        report_type=report_type,
        limit=limit,
        offset=offset,
        mismatch_only=mismatch_only,
        include_admin_state=True,
    )
    return {
        "items": [
            duty_review_service.serialize_review_item(
                row,
                fallback_report_type=report_type or "zongbao",
            )
            for row in rows
        ],
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def list_uncovered_news(*, limit: int, offset: int) -> dict[str, Any]:
    rows, total = get_adapter().fetch_uncovered_news(
        limit=limit,
        offset=offset,
    )
    return {
        "items": rows,
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def import_results(
    *,
    shift_id: str,
    article_ids: Sequence[str],
    target_status: str,
    report_type: str,
    actor: ConsoleUser,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    if not actor.user_id:
        raise PermissionError("A business administrator account is required")
    if not get_adapter().fetch_duty_shift(shift_id):
        raise ShiftNotFoundError("Duty shift not found")
    imported = get_adapter().import_shift_reviews_into_manual(
        shift_id=shift_id,
        article_ids=article_ids,
        target_status=target_status,
        report_type=report_type,
        actor_username=actor.username,
        actor_user_id=actor.user_id,
        request_id=request_id,
    )
    return {
        "imported": len(imported),
        "items": imported,
    }


def list_audit_events(
    *,
    limit: int,
    offset: int,
    actor_user_id: Optional[str] = None,
    target_type: Optional[str] = None,
) -> dict[str, Any]:
    rows, total = get_adapter().fetch_review_events(
        limit=limit,
        offset=offset,
        actor_user_id=actor_user_id,
        target_type=target_type,
    )
    return {
        "items": rows,
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


__all__ = [
    "import_results",
    "list_audit_events",
    "list_shift_results",
    "list_shift_summaries",
    "list_uncovered_news",
]
