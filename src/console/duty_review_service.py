from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_shift_reviews import (
    ShiftReviewConflictError,
    VALID_DECISIONS,
    VALID_REPORT_TYPES,
)
from src.console.auth_service import ConsoleUser
from src.console.manual_filter_serializers import serialize_manual_filter_item
from src.console.shifts_service import require_owned_shift


def serialize_review_item(
    row: Mapping[str, Any],
    *,
    fallback_report_type: str = "zongbao",
) -> dict[str, Any]:
    source = dict(row)
    source["status"] = source.get("decision") or "pending"
    source["manual_summary"] = source.get("edited_summary")
    source["manual_notes"] = source.get("notes")
    source["manual_rank"] = source.get("rank")
    item = serialize_manual_filter_item(
        source,
        fallback_status="pending",
        report_type=fallback_report_type,
    )
    item["decision"] = source["status"]
    item["version"] = source.get("version") or 0
    item["excerpt_text"] = source.get("excerpt_text")
    item["edited_summary"] = source.get("edited_summary")
    item["updated_by_user_id"] = source.get("updated_by_user_id")
    item["updated_by_display_name"] = source.get("updated_by_display_name")
    item["admin_status"] = source.get("admin_status")
    item["admin_report_type"] = source.get("admin_report_type")
    item["admin_decided_by"] = source.get("admin_decided_by")
    item["admin_decided_at"] = source.get("admin_decided_at")
    item["admin_version"] = source.get("admin_version")
    return item


def list_items(
    *,
    shift_id: str,
    user: ConsoleUser,
    decision: Optional[str],
    report_type: Optional[str],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user)
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
    )
    return {
        "items": [
            serialize_review_item(
                row,
                fallback_report_type=report_type or "zongbao",
            )
            for row in rows
        ],
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def list_clusters(
    *,
    shift_id: str,
    user: ConsoleUser,
    report_type: str,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user)
    if report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"Invalid report type: {report_type}")
    rows = get_adapter().fetch_shift_clusters(
        shift_id=shift_id,
        report_type=report_type,
    )
    clusters = [
        {
            "cluster_id": str(row["cluster_id"]),
            "bucket_key": str(row["bucket_key"]),
            "item_ids": [str(article_id) for article_id in row.get("item_ids") or []],
        }
        for row in rows
        if row.get("item_ids")
    ]
    return {
        "clusters": clusters,
        "item_total": sum(len(cluster["item_ids"]) for cluster in clusters),
    }


def save_review(
    *,
    shift_id: str,
    article_id: str,
    user: ConsoleUser,
    expected_version: Optional[int],
    patch: Mapping[str, Any],
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user)
    if not user.user_id:
        raise PermissionError("A business user account is required")
    if "decision" in patch and patch["decision"] not in VALID_DECISIONS:
        raise ValueError(f"Invalid review decision: {patch['decision']}")
    if (
        "report_type" in patch
        and patch["report_type"] is not None
        and patch["report_type"] not in VALID_REPORT_TYPES
    ):
        raise ValueError(f"Invalid report type: {patch['report_type']}")
    saved = get_adapter().save_shift_review(
        shift_id=shift_id,
        article_id=article_id,
        actor_user_id=user.user_id,
        expected_version=expected_version,
        patch=patch,
        request_id=request_id,
    )
    return serialize_review_item(saved)


def update_order(
    *,
    shift_id: str,
    user: ConsoleUser,
    selected_order: Sequence[str],
    backup_order: Sequence[str],
    request_id: Optional[str] = None,
) -> dict[str, int]:
    require_owned_shift(shift_id, user)
    if not user.user_id:
        raise PermissionError("A business user account is required")
    selected = [article_id for article_id in selected_order if article_id]
    backup = [article_id for article_id in backup_order if article_id]
    combined = selected + backup
    if len(combined) != len(set(combined)):
        raise ValueError("An article appears more than once in the order")
    updated = get_adapter().update_shift_review_order(
        shift_id=shift_id,
        actor_user_id=user.user_id,
        selected_order=selected,
        backup_order=backup,
        request_id=request_id,
    )
    return {
        "selected": len(selected),
        "backup": len(backup),
        "updated": updated,
    }


def get_stats(*, shift_id: str, user: ConsoleUser) -> dict[str, Any]:
    require_owned_shift(shift_id, user, allow_cancelled=True)
    return get_adapter().fetch_shift_stats(shift_id)


def _preview_entry(item: Mapping[str, Any], index: int) -> str:
    title = str(item.get("title") or "无标题").strip()
    summary = str(
        item.get("edited_summary")
        or item.get("excerpt_text")
        or item.get("llm_summary")
        or ""
    ).strip()
    source = str(item.get("manual_llm_source") or item.get("llm_source") or "").strip()
    lines = [f"{index}. {title}"]
    if source:
        lines.append(f"来源：{source}")
    if summary:
        lines.append(summary)
    return "\n".join(lines)


def build_preview(
    *,
    shift_id: str,
    user: ConsoleUser,
    report_type: str,
) -> dict[str, Any]:
    if report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"Invalid report type: {report_type}")
    require_owned_shift(shift_id, user, allow_cancelled=True)
    selected_rows, _ = get_adapter().fetch_shift_review_items(
        shift_id=shift_id,
        decision="selected",
        report_type=report_type,
        limit=200,
        offset=0,
    )
    backup_rows, _ = get_adapter().fetch_shift_review_items(
        shift_id=shift_id,
        decision="backup",
        report_type=report_type,
        limit=200,
        offset=0,
    )
    entries = [
        _preview_entry(item, index)
        for index, item in enumerate(selected_rows, start=1)
    ]
    if backup_rows:
        entries.append("—— 备选 ——")
        entries.extend(
            _preview_entry(item, index)
            for index, item in enumerate(backup_rows, start=1)
        )
    return {
        "report_type": report_type,
        "selected_count": len(selected_rows),
        "backup_count": len(backup_rows),
        "text": "\n\n".join(entries),
    }


__all__ = [
    "ShiftReviewConflictError",
    "build_preview",
    "get_stats",
    "list_clusters",
    "list_items",
    "save_review",
    "serialize_review_item",
    "update_order",
]
