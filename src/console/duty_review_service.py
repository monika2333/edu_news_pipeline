from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_shift_reviews import (
    ShiftReviewConflictError,
    VALID_DECISIONS,
    VALID_REPORT_TYPES,
)
from src.console import manual_filter_duplicate_service
from src.console.auth_service import ConsoleUser
from src.console.manual_filter_serializers import serialize_manual_filter_item
from src.console.shifts_service import require_owned_shift


def _version_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        str(row["article_id"]): int(row["version"])
        for row in rows
    }


def _validate_review_patch(patch: Mapping[str, Any]) -> None:
    if "decision" in patch and patch["decision"] not in VALID_DECISIONS:
        raise ValueError(f"Invalid review decision: {patch['decision']}")
    if (
        "report_type" in patch
        and patch["report_type"] is not None
        and patch["report_type"] not in VALID_REPORT_TYPES
    ):
        raise ValueError(f"Invalid report type: {patch['report_type']}")


def _validate_report_type(report_type: Optional[str]) -> None:
    if report_type is not None and report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"Invalid report type: {report_type}")


def _require_actor_id(user: ConsoleUser) -> str:
    if not user.user_id:
        raise PermissionError("A business user account is required")
    return user.user_id


def _normalize_ids(article_ids: Sequence[str]) -> list[str]:
    normalized: list[str] = []
    for raw_article_id in article_ids:
        article_id = str(raw_article_id).strip()
        if not article_id:
            raise ValueError("Article id cannot be empty")
        normalized.append(article_id)
    return normalized


def _ensure_disjoint(groups: Mapping[str, Sequence[str]]) -> None:
    seen: set[str] = set()
    for name, article_ids in groups.items():
        for article_id in article_ids:
            if article_id in seen:
                raise ValueError(
                    f"Article appears in more than one decision group: "
                    f"{article_id} ({name})"
                )
            seen.add(article_id)


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
    item["finalized_batch_id"] = source.get("finalized_batch_id")
    item["finalized_rank"] = source.get("finalized_rank")
    item["finalized_at"] = source.get("finalized_at")
    item["finalized_by_user_id"] = source.get("finalized_by_user_id")
    item["finalized_by_display_name"] = source.get(
        "finalized_by_display_name"
    )
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
    _validate_report_type(report_type)
    rows, total = get_adapter().fetch_shift_review_items(
        shift_id=shift_id,
        decision=decision,
        report_type=report_type,
        limit=limit,
        offset=offset,
        exclude_finalized=decision == "selected",
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
    _validate_report_type(report_type)
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


def check_duplicates(
    *,
    shift_id: str,
    user: ConsoleUser,
    report_type: str,
    decision: str,
) -> dict[str, Any]:
    def load_review(
        target_decision: str,
        *,
        limit: int,
        offset: int,
        report_type: str,
    ) -> dict[str, Any]:
        return list_items(
            shift_id=shift_id,
            user=user,
            decision=target_decision,
            report_type=report_type,
            limit=limit,
            offset=offset,
        )

    return manual_filter_duplicate_service.check_duplicates(
        report_type=report_type,
        decision=decision,
        review_loader=load_review,
    )


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
    actor_user_id = _require_actor_id(user)
    _validate_review_patch(patch)
    saved = get_adapter().save_shift_review(
        shift_id=shift_id,
        article_id=article_id,
        actor_user_id=actor_user_id,
        expected_version=expected_version,
        patch=patch,
        request_id=request_id,
    )
    return serialize_review_item(saved)


def save_edits(
    *,
    shift_id: str,
    user: ConsoleUser,
    edits: Mapping[str, Mapping[str, Any]],
    versions: Mapping[str, int],
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user)
    actor_user_id = _require_actor_id(user)
    updates: list[dict[str, Any]] = []
    for raw_article_id, edit in edits.items():
        article_id = str(raw_article_id).strip()
        if not article_id:
            raise ValueError("Article id cannot be empty")
        patch = {
            "edited_summary": edit.get("summary"),
            "manual_llm_source": edit.get("llm_source"),
        }
        updates.append(
            {
                "article_id": article_id,
                "expected_version": versions.get(article_id),
                "patch": patch,
            }
        )
    saved = get_adapter().save_shift_reviews(
        shift_id=shift_id,
        actor_user_id=actor_user_id,
        updates=updates,
        action="shift_review.edit",
        request_id=request_id,
    )
    return {
        "updated": len(saved),
        "versions": _version_map(saved),
    }


def bulk_decide(
    *,
    shift_id: str,
    user: ConsoleUser,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    pending_ids: Sequence[str],
    versions: Mapping[str, int],
    report_type: str,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user)
    actor_user_id = _require_actor_id(user)
    _validate_report_type(report_type)
    groups = {
        "selected": _normalize_ids(selected_ids),
        "backup": _normalize_ids(backup_ids),
        "discarded": _normalize_ids(discarded_ids),
        "pending": _normalize_ids(pending_ids),
    }
    _ensure_disjoint(groups)
    updates = [
        {
            "article_id": article_id,
            "expected_version": versions.get(article_id),
            "patch": {
                "decision": decision,
                "report_type": report_type,
            },
        }
        for decision, article_ids in groups.items()
        for article_id in article_ids
    ]
    saved = get_adapter().save_shift_reviews(
        shift_id=shift_id,
        actor_user_id=actor_user_id,
        updates=updates,
        action="shift_review.decide",
        request_id=request_id,
    )
    return {
        **{
            decision: len(article_ids)
            for decision, article_ids in groups.items()
        },
        "versions": _version_map(saved),
    }


def update_order(
    *,
    shift_id: str,
    user: ConsoleUser,
    selected_order: Sequence[str],
    backup_order: Sequence[str],
    request_id: Optional[str] = None,
) -> dict[str, int]:
    require_owned_shift(shift_id, user)
    actor_user_id = _require_actor_id(user)
    selected = [article_id for article_id in selected_order if article_id]
    backup = [article_id for article_id in backup_order if article_id]
    combined = selected + backup
    if len(combined) != len(set(combined)):
        raise ValueError("An article appears more than once in the order")
    updated = get_adapter().update_shift_review_order(
        shift_id=shift_id,
        actor_user_id=actor_user_id,
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


def finalize_selected_batch(
    *,
    shift_id: str,
    user: ConsoleUser,
    report_type: str,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user)
    actor_user_id = _require_actor_id(user)
    _validate_report_type(report_type)
    batch = get_adapter().finalize_shift_review_batch(
        shift_id=shift_id,
        report_type=report_type,
        actor_user_id=actor_user_id,
        request_id=request_id,
    )
    return {
        "batch_id": str(batch["id"]),
        "report_type": str(batch["report_type"]),
        "finalized_at": batch["finalized_at"],
        "item_count": int(batch["item_count"]),
    }


def list_finalized_batches(
    *,
    shift_id: str,
    user: ConsoleUser,
    report_type: str,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user, allow_cancelled=True)
    _validate_report_type(report_type)
    rows = get_adapter().fetch_shift_finalized_items(
        shift_id=shift_id,
        report_type=report_type,
    )
    batches: dict[str, dict[str, Any]] = {}
    for row in rows:
        batch_id = str(row["finalized_batch_id"])
        batch = batches.setdefault(
            batch_id,
            {
                "batch_id": batch_id,
                "report_type": report_type,
                "finalized_at": row.get("finalized_at"),
                "finalized_by_display_name": row.get(
                    "finalized_by_display_name"
                ),
                "items": [],
            },
        )
        batch["items"].append(
            serialize_review_item(row, fallback_report_type=report_type)
        )
    return {
        "batches": [
            {
                **batch,
                "item_count": len(batch["items"]),
            }
            for batch in batches.values()
        ],
        "total": len(rows),
    }


def restore_finalized_batch(
    *,
    shift_id: str,
    batch_id: str,
    user: ConsoleUser,
    article_id: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    require_owned_shift(shift_id, user)
    actor_user_id = _require_actor_id(user)
    normalized_article_id = (
        str(article_id).strip() if article_id is not None else None
    )
    if article_id is not None and not normalized_article_id:
        raise ValueError("Article id cannot be empty")
    return get_adapter().restore_shift_review_finalization(
        shift_id=shift_id,
        batch_id=batch_id,
        actor_user_id=actor_user_id,
        article_id=normalized_article_id,
        request_id=request_id,
    )


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
    _validate_report_type(report_type)
    require_owned_shift(shift_id, user, allow_cancelled=True)
    selected_rows, _ = get_adapter().fetch_shift_review_items(
        shift_id=shift_id,
        decision="selected",
        report_type=report_type,
        limit=200,
        offset=0,
        exclude_finalized=True,
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
    "bulk_decide",
    "build_preview",
    "check_duplicates",
    "finalize_selected_batch",
    "get_stats",
    "list_finalized_batches",
    "list_clusters",
    "list_items",
    "save_edits",
    "save_review",
    "serialize_review_item",
    "restore_finalized_batch",
    "update_order",
]
