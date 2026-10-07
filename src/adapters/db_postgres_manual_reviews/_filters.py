from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from src.adapters.db_postgres_manual_reviews._base import (
    CREATED_LOCAL_DATE_EXPRESSION,
    MANUAL_REVIEW_SELECT_COLUMNS,
    SCORE_FEEDBACK_JOIN,
    SEARCH_TEXT_EXPRESSION,
    _build_manual_review_filters,
    report_type_expr,
)
from src.adapters.db_postgres_manual_reviews._versions import (
    update_manual_review_statuses_with_versions,
)


def search_manual_candidates(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    query: Optional[str] = None,
    created_before: Optional[date] = None,
    limit: int,
    offset: int,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    report_type: Optional[str] = None,
    duty_unprocessed_only: bool = False,
    hour_from: Optional[int] = None,
    hour_to: Optional[int] = None,
    duplicate_state: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    type_expr = report_type_expr("mr")
    clauses, params = _build_manual_review_filters(
        owner_user_id=owner_user_id,
        status="pending",
        only_ready=True,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
        duty_unprocessed_only=duty_unprocessed_only,
        hour_from=hour_from,
        hour_to=hour_to,
        duplicate_state=duplicate_state,
        min_score=min_score,
        max_score=max_score,
    )
    normalized_query = (query or "").strip()
    if normalized_query:
        clauses.append(f"{SEARCH_TEXT_EXPRESSION} ILIKE %s")
        params.append(f"%{normalized_query}%")
    if created_before:
        clauses.append(f"{CREATED_LOCAL_DATE_EXPRESSION} < %s")
        params.append(created_before)
    where_sql = " AND ".join(clauses)
    count_query = f"""
        SELECT COUNT(*) AS total
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
    """
    query_sql = f"""
        SELECT
            {MANUAL_REVIEW_SELECT_COLUMNS.format(type_expr=type_expr)}
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        {SCORE_FEEDBACK_JOIN}
        WHERE {where_sql}
        ORDER BY
            ns.external_importance_score DESC NULLS LAST,
            mr.rank ASC NULLS LAST,
            ns.score DESC NULLS LAST,
            ns.publish_time_iso DESC NULLS LAST,
            mr.article_id ASC
        LIMIT %s OFFSET %s
    """
    cur.execute(count_query, tuple(params))
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    cur.execute(query_sql, tuple(params + [limit, offset]))
    rows = cur.fetchall()
    return [dict(row) for row in rows], total


def _build_manual_candidate_filters(
    *,
    owner_user_id: str,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    created_before: Optional[date] = None,
    report_type: Optional[str] = None,
    duty_unprocessed_only: bool = False,
    hour_from: Optional[int] = None,
    hour_to: Optional[int] = None,
    duplicate_state: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> Tuple[List[str], List[Any]]:
    clauses, params = _build_manual_review_filters(
        owner_user_id=owner_user_id,
        status="pending",
        only_ready=True,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
        duty_unprocessed_only=duty_unprocessed_only,
        hour_from=hour_from,
        hour_to=hour_to,
        duplicate_state=duplicate_state,
        min_score=min_score,
        max_score=max_score,
    )
    normalized_query = (query or "").strip()
    if normalized_query:
        clauses.append(f"{SEARCH_TEXT_EXPRESSION} ILIKE %s")
        params.append(f"%{normalized_query}%")
    if created_before:
        clauses.append(f"{CREATED_LOCAL_DATE_EXPRESSION} < %s")
        params.append(created_before)
    return clauses, params


def count_manual_candidates_before_date(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    created_before: Optional[date] = None,
    report_type: Optional[str] = None,
    duty_unprocessed_only: bool = False,
    hour_from: Optional[int] = None,
    hour_to: Optional[int] = None,
    duplicate_state: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> int:
    clauses, params = _build_manual_candidate_filters(
        owner_user_id=owner_user_id,
        region=region,
        sentiment=sentiment,
        query=query,
        created_before=created_before,
        report_type=report_type,
        duty_unprocessed_only=duty_unprocessed_only,
        hour_from=hour_from,
        hour_to=hour_to,
        duplicate_state=duplicate_state,
        min_score=min_score,
        max_score=max_score,
    )
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT COUNT(*) AS total
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
    """
    cur.execute(query, tuple(params))
    row = cur.fetchone() or {}
    try:
        return int(row.get("total") or 0)
    except Exception:
        return 0


def fetch_manual_candidates_before_date_for_update(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    created_before: Optional[date] = None,
    report_type: Optional[str] = None,
    duty_unprocessed_only: bool = False,
    hour_from: Optional[int] = None,
    hour_to: Optional[int] = None,
    duplicate_state: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> list[dict[str, Any]]:
    clauses, params = _build_manual_candidate_filters(
        owner_user_id=owner_user_id,
        region=region,
        sentiment=sentiment,
        query=query,
        created_before=created_before,
        report_type=report_type,
        duty_unprocessed_only=duty_unprocessed_only,
        hour_from=hour_from,
        hour_to=hour_to,
        duplicate_state=duplicate_state,
        min_score=min_score,
        max_score=max_score,
    )
    where_sql = " AND ".join(clauses)
    cur.execute(
        f"""
        SELECT mr.article_id, mr.version
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
        ORDER BY mr.article_id
        FOR UPDATE OF mr
        """,
        tuple(params),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_discarded_manual_reviews_for_update(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    query: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    decided_since: Optional[date] = None,
    batch_decided_at: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """锁定当前管理员的已放弃行，筛选子句与放弃列表完全同口径。"""
    clauses, params = _build_manual_review_filters(
        owner_user_id=owner_user_id,
        status="discarded",
        only_ready=False,
        region=region,
        sentiment=sentiment,
        report_type=None,
        query=query,
        duty_unprocessed_only=False,
        min_score=min_score,
        max_score=max_score,
        decided_since=decided_since,
        batch_decided_at=batch_decided_at,
    )
    where_sql = " AND ".join(clauses)
    cur.execute(
        f"""
        SELECT mr.article_id, mr.version
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
        ORDER BY mr.article_id
        FOR UPDATE OF mr
        """,
        tuple(params),
    )
    return [dict(row) for row in cur.fetchall()]


def restore_discarded_manual_reviews_by_filter(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    actor_username: str,
    actor_user_id: str,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    query: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    decided_since: Optional[date] = None,
    batch_decided_at: Optional[datetime] = None,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """把当前管理员符合条件的已放弃行恢复到待处理（dry_run 只计数）。

    匹配范围 = status = 'discarded' 的行 + 与放弃列表相同的筛选子句；
    写法与 `/decide` 恢复到待处理一致（rank 置空、report_type 不改写）。
    """
    targets = fetch_discarded_manual_reviews_for_update(
        cur,
        owner_user_id=owner_user_id,
        region=region,
        sentiment=sentiment,
        query=query,
        min_score=min_score,
        max_score=max_score,
        decided_since=decided_since,
        batch_decided_at=batch_decided_at,
    )
    matched = len(targets)
    if dry_run or not targets:
        return {"matched": matched, "updated": 0, "before": [], "after": []}
    updates = [
        {
            "article_id": str(row["article_id"]),
            "status": "pending",
            "rank": None,
            "report_type": None,
        }
        for row in targets
    ]
    expected_versions = {
        str(row["article_id"]): int(row["version"])
        for row in targets
    }
    before, after = update_manual_review_statuses_with_versions(
        cur,
        updates,
        owner_user_id=owner_user_id,
        actor_username=actor_username,
        actor_user_id=actor_user_id,
        expected_versions=expected_versions,
        require_versions=True,
        report_type=None,
    )
    return {
        "matched": matched,
        "updated": len(after),
        "before": before,
        "after": after,
    }


__all__ = [
    "_build_manual_candidate_filters",
    "count_manual_candidates_before_date",
    "fetch_discarded_manual_reviews_for_update",
    "fetch_manual_candidates_before_date_for_update",
    "restore_discarded_manual_reviews_by_filter",
    "search_manual_candidates",
]
