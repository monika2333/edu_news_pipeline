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
from src.console.search_terms import normalize_search_terms
from src.domain.report_type import NEWS_REPORT_TYPES


def _require_client_versions(user: ConsoleUser) -> bool:
    return user.method == "session"


def _workspace_user_id(user: ConsoleUser) -> str:
    if user.role != "admin" or not user.user_id:
        raise PermissionError("需要管理员账号才能访问工作区")
    return str(user.user_id)


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


def validate_bulk_discard_bucket(*, region: str, sentiment: str) -> None:
    """Require an explicit candidate bucket before any bulk discard."""
    if region not in {"internal", "external"}:
        raise ValueError("bulk-discard requires an explicit region")
    if sentiment not in {"positive", "negative"}:
        raise ValueError("bulk-discard requires an explicit sentiment")


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
    owner_user_id = _workspace_user_id(actor)
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
        actor_user_id=owner_user_id,
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
    owner_user_id = _workspace_user_id(actor)
    del report_type
    normalized: dict[str, dict[str, Any]] = {}
    for article_id, payload in edits.items():
        normalized_edit: dict[str, Any] = {}
        if "summary" in payload:
            normalized_edit["summary"] = payload.get("summary")
        if "llm_source" in payload:
            normalized_edit["manual_llm_source"] = (
                str(payload.get("llm_source") or "").strip()
                if payload.get("llm_source") is not None
                else None
            )
        if "notes" in payload:
            normalized_edit["notes"] = payload.get("notes")
        if "score" in payload:
            normalized_edit["score"] = payload.get("score")
        if normalized_edit:
            normalized[str(article_id)] = normalized_edit
    if not normalized:
        return {"updated": 0, "versions": {}}
    after = get_adapter().update_manual_review_summaries_as_user(
        normalized,
        actor_username=actor.username,
        actor_user_id=owner_user_id,
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
    owner_user_id = _workspace_user_id(actor)
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
        actor_user_id=owner_user_id,
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
    owner_user_id = _workspace_user_id(actor)
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
        actor_user_id=owner_user_id,
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
    created_before: Optional[date],
    dry_run: bool,
    actor: ConsoleUser,
    duty_unprocessed_only: bool = False,
    request_id: Optional[str] = None,
    hour_from: Optional[int] = None,
    hour_to: Optional[int] = None,
    duplicate_state: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> dict[str, Any]:
    owner_user_id = _workspace_user_id(actor)
    validate_bulk_discard_bucket(region=region, sentiment=sentiment)
    terms = normalize_search_terms(query) or None
    refine_filters = {
        "hour_from": hour_from,
        "hour_to": hour_to,
        "duplicate_state": duplicate_state,
        "min_score": min_score,
        "max_score": max_score,
    }
    adapter = get_adapter()
    matched = adapter.manual_reviews.count_candidates_before_date(
        owner_user_id=owner_user_id,
        region=region,
        sentiment=sentiment,
        terms=terms,
        created_before=created_before,
        report_type=None,
        duty_unprocessed_only=duty_unprocessed_only,
        **refine_filters,
    )
    if dry_run or matched <= 0:
        return {"matched": matched, "updated": 0, "skipped_finalized": 0, "discarded": []}
    after = adapter.discard_manual_candidates_before_date_as_user(
        region=region,
        sentiment=sentiment,
        terms=terms,
        created_before=created_before,
        report_type=None,
        actor_username=actor.username,
        actor_user_id=owner_user_id,
        duty_unprocessed_only=duty_unprocessed_only,
        request_id=request_id,
        **refine_filters,
    )
    # 撤回需要逐条的乐观锁版本号：清理只放弃未定稿条目，整体回退 pending 是安全的
    discarded = [
        {"article_id": str(row["article_id"]), "version": int(row["version"])}
        for row in after
    ]

    return {
        "matched": matched,
        "updated": len(discarded),
        "skipped_finalized": 0,
        "discarded": discarded,
    }


def _validate_cleanup_buckets(
    buckets: Sequence[Mapping[str, Any]],
    *,
    keys: tuple[str, str],
    allowed: tuple[frozenset[str], frozenset[str]],
    label: str,
) -> list[dict[str, str]]:
    """清理旧新闻的桶列表校验：1–4 项、值显式且合法、组合不重复。

    前端一次「清理」对应一次请求、一个事务；重复组合会让按桶拆分返回值
    的逻辑产生歧义，直接拒绝。
    """
    items = [dict(bucket) for bucket in buckets]
    if not items:
        raise ValueError(f"{label} requires at least one bucket")
    if len(items) > 4:
        raise ValueError(f"{label} accepts at most four buckets")
    seen: set[tuple[str, str]] = set()
    normalized: list[dict[str, str]] = []
    for item in items:
        first = str(item.get(keys[0]) or "").strip()
        second = str(item.get(keys[1]) or "").strip()
        if first not in allowed[0]:
            raise ValueError(f"{label} requires an explicit {keys[0]}")
        if second not in allowed[1]:
            raise ValueError(f"{label} requires an explicit {keys[1]}")
        pair = (first, second)
        if pair in seen:
            raise ValueError(f"{label} contains a duplicate bucket: {pair}")
        seen.add(pair)
        normalized.append({keys[0]: first, keys[1]: second})
    return normalized


def cleanup_candidates(
    *,
    created_before: date,
    buckets: Sequence[Mapping[str, Any]],
    dry_run: bool,
    actor: ConsoleUser,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """全量筛选页「清理旧新闻」：按多个分类一次请求、一个事务放弃。

    每个分类的匹配条件与 /bulk-discard 带 created_before、不带关键词和
    细化筛选时完全相同（dry_run 只计数）；执行走
    cleanup_manual_candidates_before_date_as_user，所有分类共享同一个
    decided_at，在放弃页显示为一个批次。
    """
    owner_user_id = _workspace_user_id(actor)
    normalized = _validate_cleanup_buckets(
        buckets,
        keys=("region", "sentiment"),
        allowed=(frozenset({"internal", "external"}), frozenset({"positive", "negative"})),
        label="cleanup-candidates",
    )
    adapter = get_adapter()
    matched_by_bucket = [
        (
            bucket,
            adapter.manual_reviews.count_candidates_before_date(
                owner_user_id=owner_user_id,
                region=bucket["region"],
                sentiment=bucket["sentiment"],
                terms=None,
                created_before=created_before,
                report_type=None,
            ),
        )
        for bucket in normalized
    ]
    total_matched = sum(count for _, count in matched_by_bucket)
    if dry_run or total_matched <= 0:
        return {
            "buckets": [
                {**bucket, "matched": count, "updated": 0}
                for bucket, count in matched_by_bucket
            ],
            "matched": total_matched,
            "updated": 0,
            "discarded": [],
        }
    return adapter.cleanup_manual_candidates_before_date_as_user(
        buckets=normalized,
        created_before=created_before,
        actor_username=actor.username,
        actor_user_id=owner_user_id,
        request_id=request_id,
    )


def cleanup_review_buckets(
    *,
    created_before: date,
    buckets: Sequence[Mapping[str, Any]],
    dry_run: bool,
    actor: ConsoleUser,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    """汇总审阅页「清理旧新闻」：多个桶一次请求、一个事务放弃。

    与 cleanup_candidates 同构，圈选维度是报别 × 采纳/备选；「旧」的判据
    是新闻入库时间。不校验检索词与细化筛选——该页面没有这些输入。
    """
    owner_user_id = _workspace_user_id(actor)
    normalized = _validate_cleanup_buckets(
        buckets,
        keys=("report_type", "status"),
        allowed=(NEWS_REPORT_TYPES, frozenset({"selected", "backup"})),
        label="cleanup-review-buckets",
    )
    adapter = get_adapter()
    matched_by_bucket = [
        (
            bucket,
            adapter.manual_reviews.count_review_bucket_before_date(
                owner_user_id=owner_user_id,
                status=bucket["status"],
                report_type=bucket["report_type"],
                created_before=created_before,
            ),
        )
        for bucket in normalized
    ]
    total_matched = sum(count for _, count in matched_by_bucket)
    if dry_run or total_matched <= 0:
        return {
            "buckets": [
                {**bucket, "matched": count, "updated": 0, "discarded": []}
                for bucket, count in matched_by_bucket
            ],
            "matched": total_matched,
            "updated": 0,
        }
    return adapter.cleanup_review_buckets_before_date_as_user(
        owner_user_id=owner_user_id,
        buckets=normalized,
        created_before=created_before,
        actor_username=actor.username,
        actor_user_id=owner_user_id,
        request_id=request_id,
    )


def bulk_restore_candidates(
    *,
    actor: ConsoleUser,
    query: Optional[str] = None,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    decided_since: Optional[date] = None,
    batch_decided_at: Optional[datetime] = None,
    dry_run: bool = True,
    request_id: Optional[str] = None,
) -> dict[str, int]:
    """把当前管理员符合条件的已放弃行恢复到待处理（dry_run 只计数）。

    与多词检索同口径：service 层切词去重，adapter 收 terms 逐词 AND。
    """
    owner_user_id = _workspace_user_id(actor)
    terms = normalize_search_terms(query) or None
    return get_adapter().restore_discarded_manual_reviews_as_user(
        actor_username=actor.username,
        actor_user_id=owner_user_id,
        region=region,
        sentiment=sentiment,
        terms=terms,
        min_score=min_score,
        max_score=max_score,
        decided_since=decided_since,
        batch_decided_at=batch_decided_at,
        dry_run=dry_run,
        request_id=request_id,
    )


def clear_review_buckets(
    *,
    owner_user_id: str,
    actor_username: str,
    actor_user_id: str,
    trigger: str,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    if owner_user_id != actor_user_id:
        raise PermissionError("只能清空当前管理员自己的工作区")
    after = get_adapter().clear_review_buckets_for_owner_as_user(
        owner_user_id=owner_user_id,
        actor_username=actor_username,
        actor_user_id=actor_user_id,
        trigger=trigger,
        request_id=request_id,
    )
    buckets = {
        "zongbao": {"selected": 0, "backup": 0},
        "wanbao": {"selected": 0, "backup": 0},
    }
    for row in after:
        report_type = _normalize_report_type(row.get("report_type"))
        previous_status = str(row.get("previous_status") or "")
        if previous_status not in {"selected", "backup"}:
            continue
        buckets[report_type][previous_status] += 1
    return {"total": len(after), "buckets": buckets}


def clear_all_review_buckets(
    *,
    actor_username: str,
    trigger: str,
    request_id: Optional[str] = None,
) -> dict[str, Any]:
    after = get_adapter().clear_all_review_buckets_as_system(
        actor_username=actor_username,
        trigger=trigger,
        request_id=request_id,
    )
    buckets = {
        "zongbao": {"selected": 0, "backup": 0},
        "wanbao": {"selected": 0, "backup": 0},
    }
    for row in after:
        report_type = _normalize_report_type(row.get("report_type"))
        previous_status = str(row.get("previous_status") or "")
        if previous_status in {"selected", "backup"}:
            buckets[report_type][previous_status] += 1
    return {"total": len(after), "buckets": buckets}


__all__ = [
    "ManualReviewConflictError",
    "archive_items",
    "bulk_decide",
    "bulk_discard_candidates",
    "bulk_restore_candidates",
    "cleanup_candidates",
    "cleanup_review_buckets",
    "clear_review_buckets",
    "clear_all_review_buckets",
    "save_edits",
    "update_ranks",
    "validate_bulk_discard_bucket",
]
