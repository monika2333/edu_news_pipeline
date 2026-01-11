from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import psycopg


def normalize_report_type_value(report_type: Optional[str]) -> Optional[str]:
    value = (report_type or "").strip().lower()
    if not value:
        return None
    if value in ("zongbao", "wanbao"):
        return value
    return "zongbao"


def report_type_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}report_type, 'zongbao')"


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
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    query = """
        INSERT INTO manual_reviews (article_id, status, report_type, summary, manual_llm_source, rank, notes, score, decided_by, decided_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (article_id) DO NOTHING
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
    status: str,
    limit: int,
    offset: int,
    only_ready: bool = False,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    report_type: Optional[str] = None,
    order_by_decided_at: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    clauses = ["mr.status = %s"]
    params: List[Any] = [status]
    type_expr = report_type_expr("mr")
    normalized_report_type = normalize_report_type_value(report_type)
    if normalized_report_type:
        clauses.append(f"{type_expr} = %s")
        params.append(normalized_report_type)
    if only_ready:
        clauses.append("ns.status = 'ready_for_export'")
    if region in ("internal", "external"):
        clauses.append("ns.is_beijing_related = %s")
        params.append(True if region == "internal" else False)
    if sentiment in ("positive", "negative"):
        clauses.append("ns.sentiment_label = %s")
        params.append(sentiment)
    where_sql = " AND ".join(clauses)
    base_params = list(params)
    count_query = f"""
        SELECT COUNT(*) AS total
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
    """
    query = f"""
        SELECT
            mr.article_id,
            mr.status,
            mr.summary AS manual_summary,
            mr.manual_llm_source,
            mr.rank AS manual_rank,
            mr.notes AS manual_notes,
            mr.score AS manual_score,
            {type_expr} AS report_type,
            mr.decided_by,
            mr.decided_at,
            ns.title,
            ns.llm_summary,
            ns.llm_source,
            ns.score,
            ns.content_markdown,
            ns.url,
            ns.source,
            ns.publish_time_iso,
            ns.publish_time,
            ns.sentiment_label,
            ns.sentiment_confidence,
            ns.is_beijing_related,
            ns.external_importance_score,
            ns.external_importance_checked_at,
            ns.score_details
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
        ORDER BY
            {"mr.decided_at DESC NULLS LAST," if order_by_decided_at else ""}
            ns.external_importance_score DESC NULLS LAST,
            mr.rank ASC NULLS LAST,
            ns.score DESC NULLS LAST,
            ns.publish_time_iso DESC NULLS LAST,
            mr.article_id ASC
        LIMIT %s OFFSET %s
    """
    cur.execute(count_query, tuple(base_params))
    total_row = cur.fetchone()
    total = int(total_row["total"]) if total_row else 0
    cur.execute(query, tuple(params + [limit, offset]))
    rows = cur.fetchall()
    items = [dict(row) for row in rows]
    return items, total


def fetch_manual_pending_for_cluster(
    cur: psycopg.Cursor,
    *,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    fetch_limit: int = 5000,
    report_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    clauses = ["mr.status = 'pending'", "ns.status = 'ready_for_export'"]
    params: List[Any] = []
    type_expr = report_type_expr("mr")
    normalized_report_type = normalize_report_type_value(report_type)
    if normalized_report_type:
        clauses.append(f"{type_expr} = %s")
        params.append(normalized_report_type)
    if region in ("internal", "external"):
        clauses.append("ns.is_beijing_related = %s")
        params.append(True if region == "internal" else False)
    if sentiment in ("positive", "negative"):
        clauses.append("ns.sentiment_label = %s")
        params.append(sentiment)
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT
            mr.article_id,
            mr.summary AS manual_summary,
            mr.manual_llm_source,
            mr.rank AS manual_rank,
            mr.notes AS manual_notes,
            mr.score AS manual_score,
            {type_expr} AS report_type,
            mr.decided_by,
            mr.decided_at,
            ns.title,
            ns.llm_summary,
            ns.llm_source,
            ns.score,
            ns.content_markdown,
            ns.url,
            ns.source,
            ns.publish_time_iso,
            ns.publish_time,
            ns.sentiment_label,
            ns.sentiment_confidence,
            ns.is_beijing_related,
            ns.external_importance_score,
            ns.external_importance_checked_at,
            ns.score_details
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
        ORDER BY ns.external_importance_score DESC NULLS LAST,
                 mr.rank ASC NULLS LAST,
                 ns.score DESC NULLS LAST,
                 ns.publish_time_iso DESC NULLS LAST,
                 mr.article_id ASC
        LIMIT %s
    """
    cur.execute(query, tuple(params + [fetch_limit]))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def manual_review_status_counts(cur: psycopg.Cursor, *, report_type: Optional[str] = None) -> Dict[str, int]:
    counts: Dict[str, int] = {"pending": 0, "selected": 0, "backup": 0, "discarded": 0, "exported": 0}
    type_expr = report_type_expr()
    normalized_report_type = normalize_report_type_value(report_type)
    params: List[Any] = []
    where_clause = ""
    if normalized_report_type:
        where_clause = f"WHERE {type_expr} = %s"
        params.append(normalized_report_type)
    query = f"""
        SELECT status, COUNT(*) AS total
        FROM manual_reviews
        {where_clause}
        GROUP BY status
    """
    cur.execute(query, tuple(params))
    for row in cur.fetchall():
        status = str(row.get("status") or "").strip() or "pending"
        try:
            counts[status] = int(row.get("total") or 0)
        except Exception:
            counts[status] = 0
    return counts


def manual_review_pending_count(cur: psycopg.Cursor, *, report_type: Optional[str] = None) -> int:
    clauses = ["mr.status = 'pending'", "ns.status = 'ready_for_export'"]
    params: List[Any] = []
    type_expr = report_type_expr("mr")
    normalized_report_type = normalize_report_type_value(report_type)
    if normalized_report_type:
        clauses.append(f"{type_expr} = %s")
        params.append(normalized_report_type)
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


def manual_review_max_rank(cur: psycopg.Cursor, status: str, *, report_type: Optional[str] = None) -> float:
    type_expr = report_type_expr()
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    query = f"SELECT COALESCE(MAX(rank), 0) AS max_rank FROM manual_reviews WHERE status = %s AND {type_expr} = %s"
    cur.execute(query, (status, normalized_report_type))
    row = cur.fetchone() or {}
    try:
        return float(row.get("max_rank") or 0.0)
    except Exception:
        return 0.0


def update_manual_review_statuses(
    cur: psycopg.Cursor,
    updates: Sequence[Mapping[str, Any]],
    *,
    report_type: Optional[str] = None,
) -> int:
    if not updates:
        return 0
    default_report_type = normalize_report_type_value(report_type)
    payload: List[Tuple[Any, ...]] = []
    for item in updates:
        article_id = str(item.get("article_id") or "").strip()
        status = str(item.get("status") or "").strip()
        if not article_id or not status:
            continue
        target_report_type = normalize_report_type_value(item.get("report_type")) or default_report_type
        payload.append(
            (
                status,
                item.get("rank"),
                item.get("decided_by"),
                item.get("decided_at"),
                target_report_type,
                article_id,
            )
        )
    if not payload:
        return 0
    query = """
        UPDATE manual_reviews
        SET status = %s,
            rank = %s,
            decided_by = COALESCE(%s, decided_by),
            decided_at = COALESCE(%s, decided_at),
            report_type = COALESCE(%s, report_type),
            updated_at = NOW()
        WHERE article_id = %s
    """
    cur.executemany(query, payload)
    return cur.rowcount


def reset_manual_reviews_to_pending(
    cur: psycopg.Cursor,
    article_ids: Sequence[str],
    *,
    actor: Optional[str] = None,
    decided_at: Optional[datetime] = None,
    report_type: Optional[str] = None,
) -> int:
    target_ids = [str(aid).strip() for aid in article_ids or [] if str(aid).strip()]
    if not target_ids:
        return 0
    timestamp = decided_at or datetime.now(timezone.utc)
    normalized_report_type = normalize_report_type_value(report_type)
    payload = [(actor, timestamp, normalized_report_type, aid) for aid in target_ids]
    query = """
        UPDATE manual_reviews
        SET status = 'pending',
            rank = NULL,
            decided_by = COALESCE(%s, decided_by),
            decided_at = %s,
            report_type = COALESCE(%s, report_type),
            updated_at = NOW()
        WHERE article_id = %s
    """
    cur.executemany(query, payload)
    return cur.rowcount


def update_manual_review_summaries(
    cur: psycopg.Cursor,
    edits: Mapping[str, Mapping[str, Any]],
    *,
    actor: Optional[str] = None,
    decided_at: Optional[datetime] = None,
    report_type: Optional[str] = None,
) -> int:
    if not edits:
        return 0
    timestamp = decided_at or datetime.now(timezone.utc)
    normalized_report_type = normalize_report_type_value(report_type)
    payload: List[Tuple[Any, ...]] = []
    for aid, edit in edits.items():
        summary = edit.get("summary")
        notes = edit.get("notes")
        score = edit.get("score")
        manual_llm_source = edit.get("manual_llm_source")
        item_report_type = normalize_report_type_value(edit.get("report_type")) or normalized_report_type
        article_id = str(aid).strip()
        if not article_id or (summary is None and manual_llm_source is None and notes is None and score is None):
            continue
        payload.append((summary, manual_llm_source, notes, score, actor, timestamp, item_report_type, article_id))
    if not payload:
        return 0
    query = """
        UPDATE manual_reviews
        SET summary = COALESCE(%s, summary),
            manual_llm_source = COALESCE(%s, manual_llm_source),
            notes = COALESCE(%s, notes),
            score = COALESCE(%s, score),
            decided_by = COALESCE(%s, decided_by),
            decided_at = COALESCE(%s, decided_at),
            report_type = COALESCE(%s, report_type),
            updated_at = NOW()
        WHERE article_id = %s
    """
    cur.executemany(query, payload)
    return cur.rowcount


def fetch_manual_selected_for_export(
    cur: psycopg.Cursor,
    *,
    report_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    type_expr = report_type_expr("mr")
    normalized_report_type = normalize_report_type_value(report_type)
    clauses = ["mr.status = 'selected'"]
    params: List[Any] = []
    if normalized_report_type:
        clauses.append(f"{type_expr} = %s")
        params.append(normalized_report_type)
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT
            mr.article_id,
            mr.summary AS manual_summary,
            mr.manual_llm_source,
            mr.rank AS manual_rank,
            mr.notes AS manual_notes,
            mr.score AS manual_score,
            {type_expr} AS report_type,
            mr.decided_by,
            mr.decided_at,
            ns.title,
            ns.llm_summary,
            ns.llm_source,
            ns.score,
            ns.content_markdown,
            ns.url,
            ns.source,
            ns.publish_time_iso,
            ns.publish_time,
            ns.sentiment_label,
            ns.sentiment_confidence,
            ns.is_beijing_related,
            ns.external_importance_score,
            ns.external_importance_checked_at
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        WHERE {where_sql}
        ORDER BY mr.rank ASC NULLS LAST,
                 mr.decided_at DESC NULLS LAST,
                 ns.external_importance_score DESC NULLS LAST,
                 ns.score DESC NULLS LAST,
                 ns.publish_time_iso DESC NULLS LAST,
                 mr.article_id ASC
    """
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "enqueue_manual_review",
    "fetch_manual_pending_for_cluster",
    "fetch_manual_reviews",
    "fetch_manual_selected_for_export",
    "manual_review_max_rank",
    "manual_review_pending_count",
    "manual_review_status_counts",
    "normalize_report_type_value",
    "report_type_expr",
    "reset_manual_reviews_to_pending",
    "update_manual_review_statuses",
    "update_manual_review_summaries",
]
