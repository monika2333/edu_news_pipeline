from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import psycopg

from src.domain.report_type import normalize_report_type as normalize_report_type_value


SEARCH_TEXT_EXPRESSION = (
    "(coalesce(ns.title, '') || ' ' || coalesce(ns.llm_summary, '') || ' ' || coalesce(ns.content_markdown, ''))"
)
CREATED_LOCAL_DATE_EXPRESSION = (
    "(ns.created_at AT TIME ZONE 'Asia/Shanghai')::date"
)
DUTY_UNPROCESSED_SQL = """
NOT EXISTS (
    SELECT 1
    FROM shift_reviews sr
    JOIN duty_shifts s ON s.id = sr.shift_id
    WHERE sr.article_id = mr.article_id
      AND s.cancelled_at IS NULL
)
""".strip()


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
    query: Optional[str] = None,
    duty_unprocessed_only: bool = False,
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
    normalized_query = (query or "").strip()
    if normalized_query:
        clauses.append(f"{SEARCH_TEXT_EXPRESSION} ILIKE %s")
        params.append(f"%{normalized_query}%")
    if duty_unprocessed_only:
        clauses.append(DUTY_UNPROCESSED_SQL)
    return clauses, params


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

__all__ = [
    "CREATED_LOCAL_DATE_EXPRESSION",
    "DUTY_UNPROCESSED_SQL",
    "MANUAL_REVIEW_DECISION_LOCK_ID",
    "MANUAL_REVIEW_SELECT_COLUMNS",
    "ManualReviewConflictError",
    "SCORE_FEEDBACK_JOIN",
    "SEARCH_TEXT_EXPRESSION",
    "_build_manual_review_filters",
    "manual_review_max_rank",
    "normalize_report_type_value",
    "report_type_expr",
]
