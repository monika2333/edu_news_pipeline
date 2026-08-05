from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_manual_reviews import ManualReviewConflictError
from src.console.auth_service import ConsoleUser
from src.console.manual_filter_helpers import (
    DEFAULT_REPORT_TYPE,
    _normalize_ids,
    _normalize_report_type,
)


def _require_client_versions(user: ConsoleUser) -> bool:
    return user.method == "session"


def _version_map(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {
        str(row["article_id"]): int(row["version"])
        for row in rows
    }


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


def bulk_decide(
    *,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    pending_ids: Sequence[str],
    versions: Mapping[str, int],
    actor: ConsoleUser,
    report_type: str = DEFAULT_REPORT_TYPE,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    selected = _normalize_ids(selected_ids)
    backup = _normalize_ids(backup_ids)
    discarded = _normalize_ids(discarded_ids)
    pending = _normalize_ids(pending_ids)
    groups = {
        "selected": selected,
        "backup": backup,
        "discarded": discarded,
        "pending": pending,
    }
    _ensure_disjoint(groups)
    target_report_type = _normalize_report_type(report_type)
    timestamp = datetime.now(timezone.utc)
    updates: list[dict[str, Any]] = []
    for article_id in selected:
        updates.append(
            {
                "article_id": article_id,
                "status": "selected",
                "rank": None,
                "report_type": target_report_type,
                "decided_at": timestamp,
            }
        )
    for article_id in backup:
        updates.append(
            {
                "article_id": article_id,
                "status": "backup",
                "rank": None,
                "report_type": target_report_type,
                "decided_at": timestamp,
            }
        )
    for status, article_ids in (("discarded", discarded), ("pending", pending)):
        updates.extend(
            {
                "article_id": article_id,
                "status": status,
                "rank": None,
                "report_type": None,
                "decided_at": timestamp,
            }
            for article_id in article_ids
        )
    after = get_adapter().update_manual_review_statuses_as_user(
        updates,
        actor_username=actor.username,
        actor_user_id=actor.user_id,
        expected_versions=versions,
        require_versions=_require_client_versions(actor),
        action="manual_review.decide",
        report_type=None,
        request_id=request_id,
    )
    return {
        **{name: len(article_ids) for name, article_ids in groups.items()},
        "versions": _version_map(after),
    }


def save_edits(
    edits: Mapping[str, Mapping[str, Any]],
    *,
    versions: Mapping[str, int],
    actor: ConsoleUser,
    report_type: str = DEFAULT_REPORT_TYPE,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    del report_type
    normalized: dict[str, dict[str, Any]] = {}
    for article_id, payload in edits.items():
        normalized[str(article_id)] = {
            "summary": payload.get("summary"),
            "manual_llm_source": (
                str(payload.get("llm_source") or "").strip()
                if payload.get("llm_source") is not None
                else None
            ),
            "notes": payload.get("notes"),
            "score": payload.get("score"),
        }
    after = get_adapter().update_manual_review_summaries_as_user(
        normalized,
        actor_username=actor.username,
        actor_user_id=actor.user_id,
        expected_versions=versions,
        require_versions=_require_client_versions(actor),
        report_type=None,
        request_id=request_id,
    )
    return {
        "updated": len(after),
        "versions": _version_map(after),
    }


def archive_items(
    article_ids: Sequence[str],
    *,
    versions: Mapping[str, int],
    actor: ConsoleUser,
    report_type: str = DEFAULT_REPORT_TYPE,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    target_ids = _normalize_ids(article_ids)
    del report_type
    timestamp = datetime.now(timezone.utc)
    updates = [
        {
            "article_id": article_id,
            "status": "exported",
            "rank": None,
            "report_type": None,
            "decided_at": timestamp,
        }
        for article_id in target_ids
    ]
    after = get_adapter().update_manual_review_statuses_as_user(
        updates,
        actor_username=actor.username,
        actor_user_id=actor.user_id,
        expected_versions=versions,
        require_versions=_require_client_versions(actor),
        action="manual_review.archive",
        report_type=None,
        request_id=request_id,
    )
    return {
        "exported": len(after),
        "versions": _version_map(after),
    }


def update_ranks(
    *,
    selected_order: Sequence[str],
    backup_order: Sequence[str],
    group_orders: Mapping[str, Sequence[str]],
    actor: ConsoleUser,
    report_type: str = DEFAULT_REPORT_TYPE,
    request_id: Optional[str] = None,
) -> dict[str, int]:
    selected_ids = _normalize_ids(selected_order)
    backup_ids = _normalize_ids(backup_order)
    _ensure_disjoint({"selected": selected_ids, "backup": backup_ids})
    ordered_ids = set(selected_ids) | set(backup_ids)
    category_updates: list[dict[str, Any]] = []
    seen_category_ids: set[str] = set()
    for group_key, group_ids in group_orders.items():
        try:
            region, sentiment = group_key.split("_", maxsplit=1)
        except ValueError as exc:
            raise ValueError(f"Invalid review group: {group_key}") from exc
        if region not in {"internal", "external"} or sentiment not in {
            "positive",
            "negative",
        }:
            raise ValueError(f"Invalid review group: {group_key}")
        for article_id in _normalize_ids(group_ids):
            if article_id not in ordered_ids:
                raise ValueError(
                    f"Grouped article is missing from review order: {article_id}"
                )
            if article_id in seen_category_ids:
                raise ValueError(
                    f"Article appears in multiple review groups: {article_id}"
                )
            seen_category_ids.add(article_id)
            category_updates.append(
                {
                    "article_id": article_id,
                    "is_beijing_related": region == "internal",
                    "sentiment_label": sentiment,
                }
            )
    target_report_type = _normalize_report_type(report_type)
    review_updates = [
        {
            "article_id": article_id,
            "status": "selected",
            "rank": float(index),
            "report_type": target_report_type,
        }
        for index, article_id in enumerate(selected_ids, start=1)
    ]
    review_updates.extend(
        {
            "article_id": article_id,
            "status": "backup",
            "rank": float(index),
            "report_type": target_report_type,
        }
        for index, article_id in enumerate(backup_ids, start=1)
    )
    if not review_updates:
        return {
            "selected": 0,
            "backup": 0,
            "updated_rows": 0,
            "updated_categories": 0,
        }
    updated_rows, updated_categories = get_adapter().update_manual_review_order_as_user(
        review_updates,
        category_updates,
        actor_username=actor.username,
        actor_user_id=actor.user_id,
        report_type=target_report_type,
        request_id=request_id,
    )
    return {
        "selected": len(selected_ids),
        "backup": len(backup_ids),
        "updated_rows": updated_rows,
        "updated_categories": updated_categories,
    }


def bulk_discard_candidates(
    *,
    region: str,
    sentiment: str,
    query: Optional[str],
    published_before: Optional[date],
    dry_run: bool,
    actor: ConsoleUser,
    request_id: Optional[str] = None,
) -> dict[str, int]:
    if region not in {"internal", "external"}:
        raise ValueError("bulk-discard requires an explicit region")
    if sentiment not in {"positive", "negative"}:
        raise ValueError("bulk-discard requires an explicit sentiment")
    normalized_query = (query or "").strip() or None
    adapter = get_adapter()
    matched = adapter.manual_reviews.count_candidates_before_date(
        region=region,
        sentiment=sentiment,
        query=normalized_query,
        published_before=published_before,
        report_type=None,
    )
    if dry_run or matched <= 0:
        return {"matched": matched, "updated": 0}
    after = adapter.discard_manual_candidates_before_date_as_user(
        region=region,
        sentiment=sentiment,
        query=normalized_query,
        published_before=published_before,
        report_type=None,
        actor_username=actor.username,
        actor_user_id=actor.user_id,
        request_id=request_id,
    )
    return {"matched": matched, "updated": len(after)}


__all__ = [
    "ManualReviewConflictError",
    "archive_items",
    "bulk_decide",
    "bulk_discard_candidates",
    "save_edits",
    "update_ranks",
]
