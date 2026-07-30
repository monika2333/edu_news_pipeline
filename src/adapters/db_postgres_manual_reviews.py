from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import psycopg

from src.domain.report_type import normalize_report_type as normalize_report_type_value

SEARCH_TEXT_EXPRESSION = (
    "(coalesce(ns.title, '') || ' ' || coalesce(ns.llm_summary, '') || ' ' || coalesce(ns.content_markdown, ''))"
)
PUBLISHED_LOCAL_DATE_EXPRESSION = (
    "COALESCE((ns.publish_time_iso AT TIME ZONE 'Asia/Shanghai')::date, "
    "timezone('Asia/Shanghai', to_timestamp(ns.publish_time))::date)"
)
MANUAL_REVIEW_SELECT_COLUMNS = """
    mr.article_id,
    mr.status,
    mr.summary AS manual_summary,
    mr.manual_llm_source,
    mr.rank AS manual_rank,
    mr.notes AS manual_notes,
    mr.score AS manual_score,
    {type_expr} AS report_type,
    mr.decided_by,
    mr.decided_by_user_id,
    mr.decided_at,
    mr.version,
    ns.title,
    ns.llm_summary,
    ns.llm_source,
    ns.score,
    ns.url,
    ns.source,
    ns.publish_time_iso,
    ns.publish_time,
    ns.sentiment_label,
    ns.sentiment_confidence,
    ns.is_beijing_related,
    ns.external_importance_score,
    ns.external_importance_checked_at,
    ns.score_details,
    sf.feedback_type AS score_feedback_type,
    sf.score_value AS score_feedback_score_value,
    sf.notes AS score_feedback_notes,
    sf.submitted_by AS score_feedback_submitted_by,
    sf.submitted_by_user_id AS score_feedback_submitted_by_user_id,
    feedback_submitter.display_name AS score_feedback_submitted_by_display_name,
    sf.updated_at AS score_feedback_updated_at
"""

SCORE_FEEDBACK_JOIN = """
    LEFT JOIN score_feedbacks sf
      ON sf.article_id = ns.article_id
     AND sf.prompt_key = ns.external_importance_raw ->> 'prompt_key'
     AND sf.prompt_version = ns.external_importance_raw ->> 'prompt_version'
    LEFT JOIN console_users feedback_submitter
      ON feedback_submitter.id = sf.submitted_by_user_id
"""


class ManualReviewConflictError(RuntimeError):
    """Raised when an administrator writes from a stale manual-review version."""


MANUAL_REVIEW_DECISION_LOCK_ID = 2_026_072_401


def report_type_expr(alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return f"COALESCE({prefix}report_type, 'zongbao')"


def _build_manual_review_filters(
    *,
    status: Optional[str] = None,
    only_ready: bool = False,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    report_type: Optional[str] = None,
    hide_submitted: bool = False,
) -> Tuple[List[str], List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if status:
        clauses.append("mr.status = %s")
        params.append(status)
    if only_ready:
        clauses.append("ns.status = 'ready_for_export'")
    type_expr = report_type_expr("mr")
    normalized_report_type = normalize_report_type_value(report_type)
    if normalized_report_type:
        clauses.append(f"{type_expr} = %s")
        params.append(normalized_report_type)
    if region in ("internal", "external"):
        clauses.append("ns.is_beijing_related = %s")
        params.append(region == "internal")
    if sentiment in ("positive", "negative"):
        clauses.append("ns.sentiment_label = %s")
        params.append(sentiment)
    if hide_submitted:
        clauses.append(
            """
            not exists (
                select 1
                from submission_duplicate_matches sdm
                where sdm.article_id = ns.article_id
                  and sdm.state in ('confirmed', 'suspected')
            )
            """
        )
    return clauses, params


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
    hide_submitted: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    type_expr = report_type_expr("mr")
    clauses, params = _build_manual_review_filters(
        status=status,
        only_ready=only_ready,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
        hide_submitted=hide_submitted,
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


def fetch_manual_pending_for_cluster(
    cur: psycopg.Cursor,
    *,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    fetch_limit: int = 5000,
    report_type: Optional[str] = None,
    hide_submitted: bool = False,
) -> List[Dict[str, Any]]:
    del report_type
    type_expr = report_type_expr("mr")
    clauses, params = _build_manual_review_filters(
        status="pending",
        only_ready=True,
        region=region,
        sentiment=sentiment,
        report_type=None,
        hide_submitted=hide_submitted,
    )
    where_sql = " AND ".join(clauses)
    query = f"""
        SELECT
            {MANUAL_REVIEW_SELECT_COLUMNS.format(type_expr=type_expr)}
        FROM manual_reviews mr
        JOIN news_summaries ns ON ns.article_id = mr.article_id
        {SCORE_FEEDBACK_JOIN}
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


def search_manual_candidates(
    cur: psycopg.Cursor,
    *,
    query: Optional[str] = None,
    published_before: Optional[date] = None,
    limit: int,
    offset: int,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    report_type: Optional[str] = None,
    hide_submitted: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    type_expr = report_type_expr("mr")
    clauses, params = _build_manual_review_filters(
        status="pending",
        only_ready=True,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
        hide_submitted=hide_submitted,
    )
    normalized_query = (query or "").strip()
    if normalized_query:
        clauses.append(f"{SEARCH_TEXT_EXPRESSION} ILIKE %s")
        params.append(f"%{normalized_query}%")
    if published_before:
        clauses.append(f"{PUBLISHED_LOCAL_DATE_EXPRESSION} < %s")
        params.append(published_before)
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
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    published_before: Optional[date] = None,
    report_type: Optional[str] = None,
) -> Tuple[List[str], List[Any]]:
    clauses, params = _build_manual_review_filters(
        status="pending",
        only_ready=True,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
    )
    normalized_query = (query or "").strip()
    if normalized_query:
        clauses.append(f"{SEARCH_TEXT_EXPRESSION} ILIKE %s")
        params.append(f"%{normalized_query}%")
    if published_before:
        clauses.append(f"{PUBLISHED_LOCAL_DATE_EXPRESSION} < %s")
        params.append(published_before)
    return clauses, params


def count_manual_candidates_before_date(
    cur: psycopg.Cursor,
    *,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    published_before: Optional[date] = None,
    report_type: Optional[str] = None,
) -> int:
    clauses, params = _build_manual_candidate_filters(
        region=region,
        sentiment=sentiment,
        query=query,
        published_before=published_before,
        report_type=report_type,
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
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    published_before: Optional[date] = None,
    report_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    clauses, params = _build_manual_candidate_filters(
        region=region,
        sentiment=sentiment,
        query=query,
        published_before=published_before,
        report_type=report_type,
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


def discard_manual_candidates_before_date(
    cur: psycopg.Cursor,
    *,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    published_before: Optional[date] = None,
    actor: Optional[str] = None,
    decided_at: Optional[datetime] = None,
    report_type: Optional[str] = None,
) -> int:
    clauses, filter_params = _build_manual_candidate_filters(
        region=region,
        sentiment=sentiment,
        query=query,
        published_before=published_before,
        report_type=report_type,
    )
    where_sql = " AND ".join(clauses)
    query = f"""
        WITH matched AS (
            SELECT mr.article_id
            FROM manual_reviews mr
            JOIN news_summaries ns ON ns.article_id = mr.article_id
            WHERE {where_sql}
        )
        UPDATE manual_reviews mr
        SET status = 'discarded',
            rank = NULL,
            decided_by = %s,
            decided_at = %s
        FROM matched
        WHERE mr.article_id = matched.article_id
    """
    params = list(filter_params)
    params.extend([actor, decided_at or datetime.now(timezone.utc)])
    cur.execute(query, tuple(params))
    return cur.rowcount


def delete_manual_clusters(cur: psycopg.Cursor, *, report_type: Optional[str] = None) -> int:
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    cur.execute("DELETE FROM manual_clusters WHERE report_type = %s", (normalized_report_type,))
    return cur.rowcount


def insert_manual_clusters(
    cur: psycopg.Cursor,
    clusters: Sequence[Mapping[str, Any]],
    *,
    report_type: Optional[str] = None,
) -> int:
    if not clusters:
        return 0
    default_report_type = normalize_report_type_value(report_type) or "zongbao"
    payload: List[Tuple[Any, ...]] = []
    for cluster in clusters:
        cluster_id = str(cluster.get("cluster_id") or "").strip()
        bucket_key = str(cluster.get("bucket_key") or "").strip()
        item_ids = cluster.get("item_ids") or []
        if not cluster_id or not bucket_key:
            continue
        target_report_type = normalize_report_type_value(cluster.get("report_type")) or default_report_type
        payload.append((target_report_type, bucket_key, cluster_id, list(item_ids)))
    if not payload:
        return 0
    query = """
        INSERT INTO manual_clusters (report_type, bucket_key, cluster_id, item_ids)
        VALUES (%s, %s, %s, %s)
    """
    cur.executemany(query, payload)
    return len(payload)


def fetch_manual_clusters(
    cur: psycopg.Cursor,
    *,
    bucket_key: Optional[str] = None,
    report_type: Optional[str] = None,
    hide_submitted: bool = False,
) -> List[Dict[str, Any]]:
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    query = """
        WITH cluster_base AS (
            SELECT cluster_id, bucket_key, item_ids
            FROM manual_clusters
            WHERE report_type = %s
              AND (%s::text IS NULL OR bucket_key = %s)
        ),
        cluster_items AS (
            SELECT cb.cluster_id, cb.bucket_key, unnest(cb.item_ids) AS article_id
            FROM cluster_base cb
        )
        SELECT
            ci.cluster_id,
            ci.bucket_key,
            mr.article_id,
            mr.version,
            mr.summary AS manual_summary,
            mr.rank AS manual_rank,
            mr.manual_llm_source,
            ns.title,
            ns.llm_summary,
            ns.llm_source,
            ns.source,
            ns.url,
            ns.score,
            ns.external_importance_score,
            ns.sentiment_label,
            ns.is_beijing_related,
            ns.publish_time_iso,
            ns.publish_time,
            ns.score_details,
            sf.feedback_type AS score_feedback_type,
            sf.score_value AS score_feedback_score_value,
            sf.notes AS score_feedback_notes,
            sf.submitted_by AS score_feedback_submitted_by,
            sf.submitted_by_user_id AS score_feedback_submitted_by_user_id,
            feedback_submitter.display_name AS score_feedback_submitted_by_display_name,
            sf.updated_at AS score_feedback_updated_at
        FROM cluster_items ci
        JOIN manual_reviews mr ON mr.article_id = ci.article_id
        JOIN news_summaries ns ON ns.article_id = ci.article_id
        LEFT JOIN score_feedbacks sf
          ON sf.article_id = ns.article_id
         AND sf.prompt_key = ns.external_importance_raw ->> 'prompt_key'
         AND sf.prompt_version = ns.external_importance_raw ->> 'prompt_version'
        LEFT JOIN console_users feedback_submitter
          ON feedback_submitter.id = sf.submitted_by_user_id
        WHERE mr.status = 'pending'
          AND ns.status = 'ready_for_export'
          AND (
              %s = FALSE
              OR NOT EXISTS (
                  SELECT 1
                  FROM submission_duplicate_matches sdm
                  WHERE sdm.article_id = ns.article_id
                    AND sdm.state IN ('confirmed', 'suspected')
              )
          )
        ORDER BY
            ci.cluster_id,
            ns.external_importance_score DESC NULLS LAST,
            mr.rank ASC NULLS LAST,
            ns.score DESC NULLS LAST
    """
    cur.execute(
        query,
        (normalized_report_type, bucket_key, bucket_key, hide_submitted),
    )
    rows = cur.fetchall()
    return [dict(row) for row in rows]


def try_advisory_lock(cur: psycopg.Cursor, lock_id: int) -> bool:
    cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (int(lock_id),))
    row = cur.fetchone() or {}
    return bool(row.get("locked"))


def release_advisory_lock(cur: psycopg.Cursor, lock_id: int) -> None:
    cur.execute("SELECT pg_advisory_unlock(%s)", (int(lock_id),))


def manual_review_status_counts(cur: psycopg.Cursor, *, report_type: Optional[str] = None) -> Dict[str, int]:
    type_expr = report_type_expr()
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    query = f"""
        SELECT
            COUNT(*) FILTER (WHERE status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE status = 'discarded') AS discarded,
            COUNT(*) FILTER (
                WHERE status = 'selected' AND {type_expr} = %s
            ) AS selected,
            COUNT(*) FILTER (
                WHERE status = 'backup' AND {type_expr} = %s
            ) AS backup,
            COUNT(*) FILTER (
                WHERE status = 'exported' AND {type_expr} = %s
            ) AS exported
        FROM manual_reviews
    """
    cur.execute(query, (normalized_report_type,) * 3)
    row = cur.fetchone() or {}
    return {
        "pending": int(row.get("pending") or 0),
        "selected": int(row.get("selected") or 0),
        "backup": int(row.get("backup") or 0),
        "discarded": int(row.get("discarded") or 0),
        "exported": int(row.get("exported") or 0),
    }


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


def preview_shift_reviews_for_manual(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_ids: Sequence[str],
) -> list[dict[str, Any]]:
    normalized_ids = [
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    ]
    if not normalized_ids:
        return []
    cur.execute(
        """
        SELECT
            sr.article_id,
            sr.decision AS duty_decision,
            sr.report_type AS duty_report_type,
            COALESCE(sr.edited_summary, ns.llm_summary, '') AS duty_summary,
            COALESCE(sr.manual_llm_source, ns.llm_source, ns.source, '') AS duty_source,
            mr.id AS existing_id,
            mr.status AS existing_status,
            mr.report_type AS existing_report_type,
            mr.version AS existing_version,
            COALESCE(mr.summary, ns.llm_summary, '') AS existing_summary,
            COALESCE(mr.manual_llm_source, ns.llm_source, ns.source, '') AS existing_source,
            ns.title,
            ns.llm_summary,
            ns.llm_source,
            ns.source
        FROM shift_reviews sr
        JOIN news_summaries ns ON ns.article_id = sr.article_id
        LEFT JOIN manual_reviews mr ON mr.article_id = sr.article_id
        WHERE sr.shift_id = %s
          AND sr.article_id = ANY(%s)
        ORDER BY array_position(%s::text[], sr.article_id)
        """,
        (shift_id, normalized_ids, normalized_ids),
    )
    return [dict(row) for row in cur.fetchall()]


def _fetch_shift_reviews_for_manual_import(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_ids: Sequence[str],
) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT
            sr.article_id,
            sr.edited_summary,
            sr.manual_llm_source,
            sr.notes,
            ns.llm_summary,
            ns.score
        FROM shift_reviews sr
        JOIN news_summaries ns ON ns.article_id = sr.article_id
        WHERE sr.shift_id = %s
          AND sr.article_id = ANY(%s)
        ORDER BY array_position(%s::text[], sr.article_id)
        FOR UPDATE OF sr
        """,
        (shift_id, article_ids, article_ids),
    )
    return [dict(row) for row in cur.fetchall()]


def _return_manual_import_row(cur: psycopg.Cursor) -> dict[str, Any]:
    row = cur.fetchone()
    if not row:
        raise ManualReviewConflictError(
            "汇总审阅内容在导入前已变化，请重新比较"
        )
    return dict(row)


def _resolved_import_text(
    resolution: Mapping[str, Any],
    key: str,
    fallback: Any,
) -> Any:
    if key not in resolution:
        return fallback
    value = resolution.get(key)
    return None if value is None else str(value)


def import_shift_reviews_into_manual(
    cur: psycopg.Cursor,
    *,
    shift_id: str,
    article_ids: Sequence[str],
    target_status: str,
    report_type: str,
    actor_username: str,
    actor_user_id: str,
    existing_reviews: Sequence[Mapping[str, Any]],
    conflict_resolutions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    normalized_ids = [
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    ]
    if not normalized_ids:
        return []
    if target_status not in {"selected", "backup"}:
        raise ValueError("Imported duty results must be selected or backup")
    normalized_report_type = normalize_report_type_value(report_type)
    if not normalized_report_type:
        raise ValueError("A report type is required")
    rows = _fetch_shift_reviews_for_manual_import(
        cur,
        shift_id=shift_id,
        article_ids=normalized_ids,
    )
    if len(rows) != len(set(normalized_ids)):
        found = {str(row["article_id"]) for row in rows}
        missing = sorted(set(normalized_ids) - found)
        raise ValueError(f"Shift reviews not found: {missing}")
    existing_by_id = {
        str(row["article_id"]): row
        for row in existing_reviews
    }
    uses_rank = target_status in {"selected", "backup"}
    next_rank = (
        manual_review_max_rank(
            cur,
            target_status,
            report_type=normalized_report_type,
        )
        if uses_rank
        else 0
    )
    imported: list[dict[str, Any]] = []
    rank_offset = 0
    for row in rows:
        article_id = str(row["article_id"])
        existing = existing_by_id.get(article_id)
        has_conflict = bool(
            existing
            and existing.get("status") != "pending"
        )
        resolution = conflict_resolutions.get(article_id, {})
        if has_conflict:
            if not resolution:
                raise ManualReviewConflictError(
                    f"新闻 {article_id} 已在汇总审阅中，请先选择保留版本"
                )
            expected_version = resolution.get("existing_version")
            if expected_version is None or int(expected_version) != int(existing["version"]):
                raise ManualReviewConflictError(
                    f"新闻 {article_id} 在导入前已被更新，请重新比较"
                )
            choice = resolution.get("choice")
            if choice not in {"existing", "duty"}:
                raise ValueError(f"Invalid import resolution for {article_id}")
            if choice == "existing":
                cur.execute(
                    """
                    UPDATE manual_reviews
                    SET
                        summary = %s,
                        manual_llm_source = %s,
                        decided_by = %s,
                        decided_by_user_id = %s,
                        decided_at = now(),
                        version = version + 1,
                        updated_at = now()
                    WHERE article_id = %s
                    RETURNING
                        id, article_id, status, summary, rank, notes, score,
                        decided_by, decided_by_user_id, decided_at,
                        manual_llm_source, report_type, version, created_at, updated_at
                    """,
                    (
                        _resolved_import_text(resolution, "summary", existing.get("summary")),
                        _resolved_import_text(
                            resolution,
                            "manual_llm_source",
                            existing.get("manual_llm_source"),
                        ),
                        actor_username,
                        actor_user_id,
                        article_id,
                    ),
                )
                imported.append(_return_manual_import_row(cur))
                continue

        if uses_rank:
            rank_offset += 1
        rank = next_rank + rank_offset if uses_rank else None
        summary = row.get("edited_summary") or row.get("llm_summary")
        source = row.get("manual_llm_source")
        if existing:
            summary = _resolved_import_text(resolution, "summary", summary)
            source = _resolved_import_text(resolution, "manual_llm_source", source)
            cur.execute(
                """
                UPDATE manual_reviews
                SET
                    status = %s,
                    summary = %s,
                    rank = %s,
                    notes = %s,
                    score = COALESCE(%s, score),
                    decided_by = %s,
                    decided_by_user_id = %s,
                    decided_at = now(),
                    manual_llm_source = %s,
                    report_type = %s,
                    version = version + 1,
                    updated_at = now()
                WHERE article_id = %s
                RETURNING
                    id, article_id, status, summary, rank, notes, score,
                    decided_by, decided_by_user_id, decided_at,
                    manual_llm_source, report_type, version, created_at, updated_at
                """,
                (
                    target_status,
                    summary,
                    rank,
                    row.get("notes"),
                    row.get("score"),
                    actor_username,
                    actor_user_id,
                    source,
                    normalized_report_type,
                    article_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO manual_reviews (
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
                    version
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, 1)
                ON CONFLICT (article_id) DO NOTHING
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
                    article_id,
                    target_status,
                    summary,
                    rank,
                    row.get("notes"),
                    row.get("score"),
                    actor_username,
                    actor_user_id,
                    source,
                    normalized_report_type,
                ),
            )
        imported.append(_return_manual_import_row(cur))
    return imported


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
    "MANUAL_REVIEW_DECISION_LOCK_ID",
    "ManualReviewConflictError",
    "allocate_manual_review_decision_ranks",
    "delete_manual_clusters",
    "enqueue_manual_review",
    "fetch_manual_clusters",
    "fetch_manual_candidates_before_date_for_update",
    "fetch_manual_pending_for_cluster",
    "fetch_manual_reviews",
    "fetch_manual_review_rows",
    "fetch_manual_selected_for_export",
    "import_shift_reviews_into_manual",
    "insert_manual_clusters",
    "manual_review_max_rank",
    "manual_review_pending_count",
    "manual_review_status_counts",
    "normalize_report_type_value",
    "preview_shift_reviews_for_manual",
    "report_type_expr",
    "reset_manual_reviews_to_pending",
    "release_advisory_lock",
    "try_advisory_lock",
    "update_manual_review_statuses",
    "update_manual_review_statuses_with_versions",
    "update_manual_review_order_as_user",
    "update_manual_review_summaries",
    "update_manual_review_summaries_with_versions",
]
