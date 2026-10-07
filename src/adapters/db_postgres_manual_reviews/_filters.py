from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg

from src.adapters.db_postgres_manual_reviews._base import (
    CREATED_LOCAL_DATE_EXPRESSION,
    MANUAL_REVIEW_SELECT_COLUMNS,
    SCORE_FEEDBACK_JOIN,
    SEARCH_TEXT_EXPRESSION,
    _build_manual_review_filters,
    report_type_expr,
)
from src.adapters.sql_search import ilike_all_clauses


def search_manual_candidates(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    terms: Optional[Sequence[str]] = None,
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
    # terms 已由 service 层切词去重；逐词一条 ILIKE（AND），按字面转义
    if terms:
        term_clauses, term_params = ilike_all_clauses(SEARCH_TEXT_EXPRESSION, terms)
        clauses.extend(term_clauses)
        params.extend(term_params)
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
    terms: Optional[Sequence[str]] = None,
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
    if terms:
        term_clauses, term_params = ilike_all_clauses(SEARCH_TEXT_EXPRESSION, terms)
        clauses.extend(term_clauses)
        params.extend(term_params)
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
    terms: Optional[Sequence[str]] = None,
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
        terms=terms,
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
    terms: Optional[Sequence[str]] = None,
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
        terms=terms,
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


__all__ = [
    "_build_manual_candidate_filters",
    "count_manual_candidates_before_date",
    "fetch_manual_candidates_before_date_for_update",
    "search_manual_candidates",
]
