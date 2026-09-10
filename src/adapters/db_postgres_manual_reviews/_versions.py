from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import psycopg

from src.adapters.db_postgres_manual_reviews._base import (
    MANUAL_REVIEW_DECISION_LOCK_ID,
    manual_review_max_rank,
)
from src.domain.report_type import normalize_report_type as normalize_report_type_value


def fetch_manual_review_rows(
    cur: psycopg.Cursor,
    article_ids: Sequence[str],
    *,
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
            version,
            created_at,
            updated_at
        FROM manual_reviews
        WHERE article_id = ANY(%s)
        ORDER BY article_id
        {lock_sql}
        """,
        (normalized_ids,),
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
    before = fetch_manual_review_rows(cur, article_ids, for_update=True)
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
            WHERE article_id = %s
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
    before = fetch_manual_review_rows(cur, article_ids, for_update=True)
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
            WHERE article_id = %s
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


def update_manual_review_order_as_user(
    cur: psycopg.Cursor,
    updates: Sequence[Mapping[str, Any]],
    *,
    actor_username: str,
    actor_user_id: Optional[str],
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
        raise ValueError("A manual review appears more than once in one order")
    before = fetch_manual_review_rows(cur, article_ids, for_update=True)
    if len(before) != len(article_ids):
        found = {str(row["article_id"]) for row in before}
        missing = sorted(set(article_ids) - found)
        raise ValueError(f"Manual reviews not found: {missing}")
    default_report_type = normalize_report_type_value(report_type)
    after: list[dict[str, Any]] = []
    for item in normalized_updates:
        article_id = str(item["article_id"]).strip()
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
                decided_at = COALESCE(decided_at, now()),
                report_type = COALESCE(%s, report_type),
                updated_at = now()
            WHERE article_id = %s
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
                article_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Manual review not found: {article_id}")
        after.append(dict(row))
    return before, after

__all__ = [
    "_validate_expected_versions",
    "allocate_manual_review_decision_ranks",
    "fetch_manual_review_rows",
    "update_manual_review_order_as_user",
    "update_manual_review_statuses_with_versions",
    "update_manual_review_summaries_with_versions",
]
