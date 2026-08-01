from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_manual_reviews import ManualReviewConflictError
from src.adapters.db_postgres_shift_reviews import (
    VALID_DECISIONS,
    VALID_REPORT_TYPES,
)
from src.console import duty_review_service
from src.console.auth_service import ConsoleUser
from src.console.shifts_service import ShiftNotFoundError

_COLUMN_COUNT_FIELDS = (
    "zongbao_selected",
    "zongbao_backup",
    "wanbao_selected",
    "wanbao_backup",
)


def _serialize_admin_result(
    row: dict[str, Any],
    *,
    fallback_report_type: str = "zongbao",
) -> dict[str, Any]:
    item = duty_review_service.serialize_review_item(
        row,
        fallback_report_type=fallback_report_type,
    )
    item["admin_discarded_at"] = row.get("admin_discarded_at")
    item["admin_discarded_by_user_id"] = row.get(
        "admin_discarded_by_user_id"
    )
    item["admin_discarded_by_display_name"] = row.get(
        "admin_discarded_by_display_name"
    )
    return item


def list_shift_summaries(
    *,
    limit: int = 60,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    rows = [
        row
        for row in get_adapter().shift_reviews.fetch_admin_summaries(limit=limit)
        if row.get("starts_at") and row["starts_at"] <= current
    ]
    rows.sort(
        key=lambda row: row.get("ends_at") or row["starts_at"],
        reverse=True,
    )
    return [
        {
            **row,
            **{
                field: int(row.get(field) or 0)
                for field in _COLUMN_COUNT_FIELDS
            },
            "total": int(row.get("total") or 0),
            "pending": int(row.get("pending") or 0),
            "selected": int(row.get("selected") or 0),
            "backup": int(row.get("backup") or 0),
            "discarded": int(row.get("discarded") or 0),
        }
        for row in rows[: max(1, min(limit, 365))]
    ]


def list_shift_results(
    *,
    shift_id: str,
    decision: Optional[str],
    report_type: Optional[str],
    mismatch_only: bool,
    limit: int,
    offset: int,
    admin_discarded_only: bool = False,
    admin_unprocessed_only: bool = False,
    include_admin_discarded: bool = False,
) -> dict[str, Any]:
    if not get_adapter().shifts.fetch(shift_id):
        raise ShiftNotFoundError("Duty shift not found")
    if decision and decision not in VALID_DECISIONS:
        raise ValueError(f"Invalid review decision: {decision}")
    if report_type and report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"Invalid report type: {report_type}")
    exclude_admin_discarded = (
        not admin_discarded_only and not include_admin_discarded
    )
    only_admin_unprocessed = (
        admin_unprocessed_only and not admin_discarded_only
    )
    rows, total = get_adapter().shift_reviews.fetch_items(
        shift_id=shift_id,
        decision=None if admin_discarded_only else decision,
        report_type=None if admin_discarded_only else report_type,
        limit=limit,
        offset=offset,
        mismatch_only=mismatch_only and not admin_discarded_only,
        include_admin_state=True,
        admin_discarded_only=admin_discarded_only,
        exclude_admin_discarded=exclude_admin_discarded,
        admin_unprocessed_only=only_admin_unprocessed,
    )
    return {
        "items": [
            _serialize_admin_result(
                row,
                fallback_report_type=report_type or "zongbao",
            )
            for row in rows
        ],
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def set_admin_discarded(
    *,
    shift_id: str,
    article_id: str,
    discarded: bool,
    actor: ConsoleUser,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    if not actor.user_id:
        raise PermissionError("需要管理员账号才能执行该操作")
    adapter = get_adapter()
    if not adapter.shifts.fetch(shift_id):
        raise ShiftNotFoundError("Duty shift not found")
    saved = adapter.set_shift_review_admin_discarded(
        shift_id=shift_id,
        article_id=article_id,
        actor_user_id=actor.user_id,
        discarded=discarded,
        request_id=request_id,
    )
    return {
        "item": _serialize_admin_result(saved),
        "discarded": discarded,
    }


def set_admin_discarded_many(
    *,
    shift_id: str,
    article_ids: Sequence[str],
    discarded: bool,
    actor: ConsoleUser,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    if not actor.user_id:
        raise PermissionError("需要管理员账号才能执行该操作")
    normalized_ids = list(dict.fromkeys(
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    ))
    if not normalized_ids:
        raise ValueError("请至少选择一条新闻")
    adapter = get_adapter()
    if not adapter.shifts.fetch(shift_id):
        raise ShiftNotFoundError("Duty shift not found")
    saved = adapter.set_shift_reviews_admin_discarded(
        shift_id=shift_id,
        article_ids=normalized_ids,
        actor_user_id=actor.user_id,
        discarded=discarded,
        request_id=request_id,
    )
    return {
        "items": [_serialize_admin_result(item) for item in saved],
        "discarded": discarded,
        "updated": len(saved),
    }


def preview_import_results(
    *,
    shift_id: str,
    article_ids: Sequence[str],
) -> dict[str, Any]:
    adapter = get_adapter()
    if not adapter.shifts.fetch(shift_id):
        raise ShiftNotFoundError("Duty shift not found")
    normalized_ids = {
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    }
    rows = adapter.preview_shift_reviews_for_manual(
        shift_id=shift_id,
        article_ids=article_ids,
    )
    found_ids = {str(row["article_id"]) for row in rows}
    if found_ids != normalized_ids:
        missing = sorted(normalized_ids - found_ids)
        raise ValueError(f"Shift reviews not found: {missing}")
    conflicts = [
        {
            "article_id": str(row["article_id"]),
            "title": row.get("title") or "无标题",
            "existing": {
                "summary": row.get("existing_summary") or "",
                "manual_llm_source": row.get("existing_source") or "",
                "status": row.get("existing_status") or "pending",
                "report_type": row.get("existing_report_type") or "zongbao",
                "version": int(row.get("existing_version") or 0),
            },
            "duty": {
                "summary": row.get("duty_summary") or "",
                "manual_llm_source": row.get("duty_source") or "",
                "decision": row.get("duty_decision") or "pending",
                "report_type": row.get("duty_report_type") or "zongbao",
            },
        }
        for row in rows
        if row.get("existing_id")
        and row.get("existing_status") != "pending"
    ]
    return {
        "total": len(rows),
        "ready_count": len(rows) - len(conflicts),
        "conflicts": conflicts,
    }


def import_results(
    *,
    shift_id: str,
    article_ids: Sequence[str],
    target_status: str,
    report_type: str,
    actor: ConsoleUser,
    conflict_resolutions: Sequence[dict[str, Any]],
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    if not actor.user_id:
        raise PermissionError("A business administrator account is required")
    if not get_adapter().shifts.fetch(shift_id):
        raise ShiftNotFoundError("Duty shift not found")
    imported = get_adapter().import_shift_reviews_into_manual(
        shift_id=shift_id,
        article_ids=article_ids,
        target_status=target_status,
        report_type=report_type,
        actor_username=actor.username,
        actor_user_id=actor.user_id,
        conflict_resolutions=conflict_resolutions,
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
    "ManualReviewConflictError",
    "import_results",
    "list_audit_events",
    "list_shift_results",
    "list_shift_summaries",
    "preview_import_results",
    "set_admin_discarded",
]
