from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import psycopg

from src.adapters.db_postgres_manual_reviews._base import (
    MANUAL_REVIEW_DECISION_LOCK_ID,
    REVIEW_CATEGORY_OVERRIDE_KEYS,
    ManualReviewConflictError,
    category_override_value,
    fetch_news_summary_categories,
    manual_review_max_rank,
)
from src.domain.report_type import normalize_report_type as normalize_report_type_value


def fetch_manual_review_rows(
    cur: psycopg.Cursor,
    article_ids: Sequence[str],
    *,
    owner_user_id: str,
    for_update: bool = False,
) -> list[dict[str, Any]]:
    normalized_ids = [
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    ]
    if not normalized_ids:
        return []
    lock_sql = "FOR UPDATE" if for_update else ""
    cur.execute(
        f"""
        SELECT
            id,
            article_id,
            status,
            summary,
            rank,
            notes,
            score,
            decided_by,
            decided_by_user_id,
            decided_at,
            manual_llm_source,
            report_type,
            category_override,
            version,
            created_at,
            updated_at
        FROM manual_reviews
        WHERE owner_user_id = %s
          AND article_id = ANY(%s)
        ORDER BY article_id
        {lock_sql}
        """,
        (owner_user_id, normalized_ids),
    )
    return [dict(row) for row in cur.fetchall()]


def _validate_expected_versions(
    rows: Sequence[Mapping[str, Any]],
    expected_versions: Mapping[str, int],
    *,
    require_versions: bool,
) -> None:
    for row in rows:
        article_id = str(row["article_id"])
        expected = expected_versions.get(article_id)
        if expected is None:
            if require_versions:
                raise ManualReviewConflictError(
                    f"Missing review version for {article_id}"
                )
            continue
        if int(row["version"]) != int(expected):
            raise ManualReviewConflictError(
                f"Manual review version is stale for {article_id}"
            )


def allocate_manual_review_decision_ranks(
    cur: psycopg.Cursor,
    updates: Sequence[Mapping[str, Any]],
    *,
    owner_user_id: str,
    report_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT pg_advisory_xact_lock(%s)",
        (MANUAL_REVIEW_DECISION_LOCK_ID,),
    )
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    next_rank: dict[tuple[str, str], float] = {}
    allocated: list[dict[str, Any]] = []
    for source in updates:
        item = dict(source)
        status = str(item.get("status") or "").strip()
        if status not in {"selected", "backup"}:
            allocated.append(item)
            continue
        target_report_type = (
            normalize_report_type_value(item.get("report_type"))
            or normalized_report_type
        )
        key = (status, target_report_type)
        if key not in next_rank:
            next_rank[key] = manual_review_max_rank(
                cur,
                status,
                owner_user_id=owner_user_id,
                report_type=target_report_type,
            )
        next_rank[key] += 1
        item["rank"] = next_rank[key]
        allocated.append(item)
    return allocated


def update_manual_review_statuses_with_versions(
    cur: psycopg.Cursor,
    updates: Sequence[Mapping[str, Any]],
    *,
    owner_user_id: str,
    actor_username: str,
    actor_user_id: Optional[str],
    expected_versions: Mapping[str, int],
    require_versions: bool,
    report_type: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_updates = [
        dict(item)
        for item in updates
        if str(item.get("article_id") or "").strip()
        and str(item.get("status") or "").strip()
    ]
    article_ids = [str(item["article_id"]).strip() for item in normalized_updates]
    if not article_ids:
        return [], []
    if len(article_ids) != len(set(article_ids)):
        raise ValueError("A manual review appears more than once in one update")
    before = fetch_manual_review_rows(
        cur,
        article_ids,
        owner_user_id=owner_user_id,
        for_update=True,
    )
    if len(before) != len(article_ids):
        found = {str(row["article_id"]) for row in before}
        missing = sorted(set(article_ids) - found)
        raise ValueError(f"Manual reviews not found: {missing}")
    _validate_expected_versions(
        before,
        expected_versions,
        require_versions=require_versions,
    )
    before_by_id = {str(row["article_id"]): row for row in before}
    default_report_type = normalize_report_type_value(report_type)
    after: list[dict[str, Any]] = []
    for item in normalized_updates:
        article_id = str(item["article_id"]).strip()
        current = before_by_id[article_id]
        target_report_type = (
            normalize_report_type_value(item.get("report_type"))
            or default_report_type
        )
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = %s,
                rank = %s,
                decided_by = %s,
                decided_by_user_id = %s,
                decided_at = COALESCE(%s, now()),
                report_type = COALESCE(%s, report_type),
                version = version + 1,
                updated_at = now()
            WHERE owner_user_id = %s
              AND article_id = %s
              AND version = %s
            RETURNING
                id,
                article_id,
                status,
                summary,
                rank,
                notes,
                score,
                decided_by,
                decided_by_user_id,
                decided_at,
                manual_llm_source,
                report_type,
                category_override,
                version,
                created_at,
                updated_at
            """,
            (
                str(item["status"]).strip(),
                item.get("rank"),
                actor_username,
                actor_user_id,
                item.get("decided_at"),
                target_report_type,
                owner_user_id,
                article_id,
                current["version"],
            ),
        )
        row = cur.fetchone()
        if not row:
            raise ManualReviewConflictError(
                f"Manual review version is stale for {article_id}"
            )
        after.append(dict(row))
    return before, after


def update_manual_review_summaries_with_versions(
    cur: psycopg.Cursor,
    edits: Mapping[str, Mapping[str, Any]],
    *,
    owner_user_id: str,
    actor_username: str,
    actor_user_id: Optional[str],
    expected_versions: Mapping[str, int],
    require_versions: bool,
    report_type: Optional[str] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized_edits = {
        str(article_id).strip(): dict(edit)
        for article_id, edit in edits.items()
        if str(article_id).strip()
    }
    if not normalized_edits:
        return [], []
    article_ids = list(normalized_edits)
    before = fetch_manual_review_rows(
        cur,
        article_ids,
        owner_user_id=owner_user_id,
        for_update=True,
    )
    if len(before) != len(article_ids):
        found = {str(row["article_id"]) for row in before}
        missing = sorted(set(article_ids) - found)
        raise ValueError(f"Manual reviews not found: {missing}")
    _validate_expected_versions(
        before,
        expected_versions,
        require_versions=require_versions,
    )
    default_report_type = normalize_report_type_value(report_type)
    after: list[dict[str, Any]] = []
    for current in before:
        article_id = str(current["article_id"])
        edit = normalized_edits[article_id]
        target_report_type = (
            normalize_report_type_value(edit.get("report_type"))
            or default_report_type
        )
        summary = edit["summary"] if "summary" in edit else current.get("summary")
        manual_llm_source = (
            edit["manual_llm_source"]
            if "manual_llm_source" in edit
            else current.get("manual_llm_source")
        )
        notes = edit["notes"] if "notes" in edit else current.get("notes")
        score = edit["score"] if "score" in edit else current.get("score")
        cur.execute(
            """
            UPDATE manual_reviews
            SET summary = %s,
                manual_llm_source = %s,
                notes = %s,
                score = %s,
                decided_by = %s,
                decided_by_user_id = %s,
                decided_at = now(),
                report_type = COALESCE(%s, report_type),
                version = version + 1,
                updated_at = now()
            WHERE owner_user_id = %s
              AND article_id = %s
              AND version = %s
            RETURNING
                id,
                article_id,
                status,
                summary,
                rank,
                notes,
                score,
                decided_by,
                decided_by_user_id,
                decided_at,
                manual_llm_source,
                report_type,
                category_override,
                version,
                created_at,
                updated_at
            """,
            (
                summary,
                manual_llm_source,
                notes,
                score,
                actor_username,
                actor_user_id,
                target_report_type,
                owner_user_id,
                article_id,
                current["version"],
            ),
        )
        row = cur.fetchone()
        if not row:
            raise ManualReviewConflictError(
                f"Manual review version is stale for {article_id}"
            )
        after.append(dict(row))
    return before, after


def _normalize_category_updates(
    category_updates: Sequence[Mapping[str, Any]],
    *,
    ordered_article_ids: Sequence[str],
) -> dict[str, str]:
    """校验并归一化「分组键覆盖」更新：article_id -> ReviewGroupKey。"""
    ordered_ids = set(ordered_article_ids)
    result: dict[str, str] = {}
    for item in category_updates:
        article_id = str(item.get("article_id") or "").strip()
        group_key = str(item.get("category_override") or "").strip()
        if not article_id:
            raise ValueError("A review group update is missing its article id")
        if article_id not in ordered_ids:
            raise ValueError(
                f"Grouped article is missing from review order: {article_id}"
            )
        if article_id in result:
            raise ValueError(
                f"Article appears in multiple review groups: {article_id}"
            )
        if group_key not in REVIEW_CATEGORY_OVERRIDE_KEYS:
            raise ValueError(f"Invalid review group: {group_key}")
        result[article_id] = group_key
    return result


def update_manual_review_order_as_user(
    cur: psycopg.Cursor,
    updates: Sequence[Mapping[str, Any]],
    *,
    owner_user_id: str,
    actor_username: str,
    actor_user_id: Optional[str],
    report_type: Optional[str] = None,
    category_updates: Sequence[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """保存审阅排序，并在同一条 UPDATE 里写入本工作区的分类覆盖值。

    换组只写 ``manual_reviews.category_override``（NULL = 跟随全局），不再改
    ``news_summaries``。与全局分类比较后相同则清空覆盖、不同则写分组键；
    覆盖值与排序/状态写入落在同一条 SQL 里，update 之前/之后的行都带
    ``category_override``，审计事件因此能看到覆盖值的前后变化。
    """
    normalized_updates = [
        dict(item)
        for item in updates
        if str(item.get("article_id") or "").strip()
        and str(item.get("status") or "").strip()
    ]
    article_ids = [str(item["article_id"]).strip() for item in normalized_updates]
    if not article_ids:
        return [], [], 0
    if len(article_ids) != len(set(article_ids)):
        raise ValueError("A manual review appears more than once in one order")
    category_map = _normalize_category_updates(
        category_updates,
        ordered_article_ids=article_ids,
    )
    before = fetch_manual_review_rows(
        cur,
        article_ids,
        owner_user_id=owner_user_id,
        for_update=True,
    )
    if len(before) != len(article_ids):
        found = {str(row["article_id"]) for row in before}
        missing = sorted(set(article_ids) - found)
        raise ValueError(f"Manual reviews not found: {missing}")
    before_by_id = {str(row["article_id"]): row for row in before}
    global_categories = fetch_news_summary_categories(cur, list(category_map))
    default_report_type = normalize_report_type_value(report_type)
    after: list[dict[str, Any]] = []
    for item in normalized_updates:
        article_id = str(item["article_id"]).strip()
        target_report_type = (
            normalize_report_type_value(item.get("report_type"))
            or default_report_type
        )
        override = before_by_id[article_id].get("category_override")
        if article_id in category_map:
            global_category = global_categories.get(article_id) or {}
            override = category_override_value(
                category_map[article_id],
                global_is_beijing=global_category.get("is_beijing_related"),
                global_sentiment=global_category.get("sentiment_label"),
            )
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = %s,
                rank = %s,
                decided_by = %s,
                decided_by_user_id = %s,
                decided_at = COALESCE(decided_at, now()),
                report_type = COALESCE(%s, report_type),
                category_override = %s,
                updated_at = now()
            WHERE owner_user_id = %s
              AND article_id = %s
            RETURNING
                id,
                article_id,
                status,
                summary,
                rank,
                notes,
                score,
                decided_by,
                decided_by_user_id,
                decided_at,
                manual_llm_source,
                report_type,
                category_override,
                version,
                created_at,
                updated_at
            """,
            (
                str(item["status"]).strip(),
                item.get("rank"),
                actor_username,
                actor_user_id,
                target_report_type,
                override,
                owner_user_id,
                article_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Manual review not found: {article_id}")
        after.append(dict(row))
    return before, after, len(category_map)


__all__ = [
    "_normalize_category_updates",
    "_validate_expected_versions",
    "allocate_manual_review_decision_ranks",
    "fetch_manual_review_rows",
    "update_manual_review_order_as_user",
    "update_manual_review_statuses_with_versions",
    "update_manual_review_summaries_with_versions",
]
