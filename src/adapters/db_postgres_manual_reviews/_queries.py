from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from src.adapters.db_postgres_manual_reviews._base import (
    MANUAL_REVIEW_SELECT_COLUMNS,
    SCORE_FEEDBACK_JOIN,
    _build_manual_review_filters,
    report_type_expr,
)
from src.domain.report_type import normalize_report_type as normalize_report_type_value


def _manual_review_order_by(*, status: str, order_by_decided_at: bool) -> str:
    parts: List[str] = []
    if order_by_decided_at:
        parts.append("mr.decided_at DESC NULLS LAST")
    if status in ("selected", "backup"):
        parts.extend(
            [
                "mr.rank ASC NULLS LAST",
                "ns.external_importance_score DESC NULLS LAST",
            ]
        )
    else:
        parts.extend(
            [
                "ns.external_importance_score DESC NULLS LAST",
                "mr.rank ASC NULLS LAST",
            ]
        )
    parts.extend(
        [
            "ns.score DESC NULLS LAST",
            "ns.publish_time_iso DESC NULLS LAST",
            "mr.article_id ASC",
        ]
    )
    return ",\n            ".join(parts)




def enqueue_manual_review(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    status: str = "pending",
    report_type: Optional[str] = None,
    rank: Optional[float] = None,
    summary: Optional[str] = None,
    notes: Optional[str] = None,
    score: Optional[float] = None,
    decided_by: Optional[str] = None,
    decided_at: Optional[datetime] = None,
) -> None:
    if not article_id:
        return
    cur.execute(
        """
        SELECT external_importance_score, external_importance_checked_at
        FROM news_summaries
        WHERE article_id = %s
        """,
        (article_id,),
    )
    article = cur.fetchone()
    if not article:
        raise ValueError(f"Unable to enqueue missing news summary {article_id}")
    if (
        article.get("external_importance_score") is None
        or article.get("external_importance_checked_at") is None
    ):
        raise ValueError(
            f"Manual review requires completed external importance scoring for {article_id}"
        )
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    query = """
        INSERT INTO manual_reviews (
            owner_user_id,
            article_id,
            status,
            report_type,
            summary,
            manual_llm_source,
            rank,
            notes,
            score,
            decided_by,
            decided_at
        )
        SELECT
            admin.id,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        FROM active_console_admins admin
        ON CONFLICT (owner_user_id, article_id) DO NOTHING
    """
    cur.execute(
        query,
        (
            article_id,
            status or "pending",
            normalized_report_type,
            summary,
            None,
            rank,
            notes,
            score,
            decided_by,
            decided_at,
        ),
    )


def fetch_manual_reviews(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
    status: str,
    limit: int,
    offset: int,
    only_ready: bool = False,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    report_type: Optional[str] = None,
    order_by_decided_at: bool = False,
    query: Optional[str] = None,
    duty_unprocessed_only: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    type_expr = report_type_expr("mr")
    clauses, params = _build_manual_review_filters(
        owner_user_id=owner_user_id,
        status=status,
        only_ready=only_ready,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
        query=query,
        duty_unprocessed_only=duty_unprocessed_only,
    )
    where_sql = " AND ".join(clauses)
    order_by_sql = _manual_review_order_by(status=status, order_by_decided_at=order_by_decided_at)
    base_params = list(params)
    count_query = f"""
        SELECT COUNT(*) AS total
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
    """
    query = f"""
        SELECT
            {MANUAL_REVIEW_SELECT_COLUMNS.format(type_expr=type_expr)}
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        {SCORE_FEEDBACK_JOIN}
        WHERE {where_sql}
        ORDER BY
            {order_by_sql}
        LIMIT %s OFFSET %s
    """
    cur.execute(count_query, tuple(base_params))
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    cur.execute(query, tuple(params + [limit, offset]))
    rows = cur.fetchall()
    items = [dict(row) for row in rows]
    return items, total


def fetch_manual_cluster_sources(
    cur: psycopg.Cursor,
    *,
    fetch_limit: int = 5000,
) -> List[Dict[str, Any]]:
    query = """
        WITH recent AS (
            SELECT *
            FROM news_summaries
            WHERE status = 'ready_for_export'
            ORDER BY created_at DESC, article_id
            LIMIT %s
        )
        SELECT
            article_id,
            title,
            score,
            external_importance_score,
            sentiment_label,
            is_beijing_related,
            publish_time_iso,
            publish_time,
            created_at
        FROM recent
    """
    cur.execute(query, (max(1, int(fetch_limit)),))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def fetch_review_buckets_for_update(
    cur: psycopg.Cursor,
    *,
    owner_user_id: str,
) -> list[dict[str, Any]]:
    type_expr = report_type_expr("mr")
    cur.execute(
        f"""
        SELECT
            mr.article_id,
            mr.version,
            mr.status AS previous_status,
            {type_expr} AS report_type
        FROM manual_reviews mr
        WHERE mr.owner_user_id = %s
          AND mr.status IN (%s, %s)
        ORDER BY mr.article_id
        FOR UPDATE OF mr
        """,
        (owner_user_id, "selected", "backup"),
    )
    return [dict(row) for row in cur.fetchall()]

__all__ = [
    "_manual_review_order_by",
    "enqueue_manual_review",
    "fetch_manual_cluster_sources",
    "fetch_manual_reviews",
    "fetch_review_buckets_for_update",
]
