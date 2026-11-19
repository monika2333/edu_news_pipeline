from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg

from src.adapters.sql_candidate_filters import (
    candidate_extra_filter_clauses,
    decided_at_filter_clauses,
)
from src.adapters.sql_search import ilike_all_clauses
from src.domain.report_type import normalize_report_type as normalize_report_type_value


SEARCH_TEXT_EXPRESSION = (
    "(coalesce(ns.title, '') || ' ' || coalesce(ns.llm_summary, '') || ' ' || "
    "coalesce(ns.content_markdown, '') || ' ' || coalesce(ns.source, ''))"
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

# 分类覆盖列的合法取值，与前端/路由的分组键 ReviewGroupKey 同一套。
# 迁移里两张表各有同值的 CHECK 约束；这里再集中一份供 Python 侧校验与比较。
REVIEW_CATEGORY_OVERRIDE_KEYS = (
    "internal_positive",
    "internal_negative",
    "external_positive",
    "external_negative",
)


def review_category_expressions(alias: str) -> Tuple[str, str]:
    """工作区「生效分类」的 SQL 表达式（覆盖值优先，NULL 跟随全局）。

    返回 ``(is_beijing_related, sentiment_label)`` 两个表达式。调用方在 SELECT
    中把它们分别用 ``AS is_beijing_related`` / ``AS sentiment_label`` 输出同名
    别名，上层与前端因此不需要感知覆盖值的存在；WHERE 过滤也引用同一对表达式，
    保证列表、筛选、计数、批量操作同口径。

    ``alias`` 是工作区表（``manual_reviews`` / ``shift_reviews``）的别名，
    全局分类固定读 ``news_summaries ns``。覆盖列只有一个文本列，取值形如
    ``internal_positive``；NULL 表示跟随全局。这里集中构造，避免在十几处查询里
    手抄 CASE。
    """
    prefix = f"{alias}." if alias else ""
    override = f"{prefix}category_override"
    is_beijing_expression = (
        f"CASE WHEN {override} IS NULL THEN ns.is_beijing_related "
        f"WHEN split_part({override}, '_', 1) = 'internal' THEN TRUE "
        f"ELSE FALSE END"
    )
    sentiment_expression = (
        f"CASE WHEN {override} IS NULL THEN ns.sentiment_label "
        f"WHEN split_part({override}, '_', 2) = 'negative' THEN 'negative' "
        f"ELSE 'positive' END"
    )
    return is_beijing_expression, sentiment_expression


def category_override_value(
    group_key: str,
    *,
    global_is_beijing: Any,
    global_sentiment: Any,
) -> Optional[str]:
    """把拖入的分组键换算成覆盖值；与全局分类一致时返回 None（清空覆盖）。

    比较口径与展示一致：全局 ``is_beijing_related`` 非 True 视为京外，
    ``sentiment_label`` 不是 negative（含 NULL）视为正面。这样才能保证「拖回
    与全局一致的分组 → 覆盖值清空 → 回到跟随全局」，不留冗余覆盖。
    """
    normalized_group = str(group_key or "").strip()
    if normalized_group not in REVIEW_CATEGORY_OVERRIDE_KEYS:
        raise ValueError(f"Invalid review group: {normalized_group}")
    group_region, group_sentiment = normalized_group.split("_", maxsplit=1)
    global_region = "internal" if global_is_beijing else "external"
    global_sentiment_key = (
        "negative"
        if str(global_sentiment or "").strip().lower() == "negative"
        else "positive"
    )
    if group_region == global_region and group_sentiment == global_sentiment_key:
        return None
    return normalized_group


def fetch_news_summary_categories(
    cur: psycopg.Cursor,
    article_ids: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """读取条目当前的全局分类，供写入侧判断是否需要落覆盖值。

    返回 ``article_id -> {is_beijing_related, sentiment_label}``。条目不存在的
    情况理论上前置读取（列表/审阅）已会失败，这里按「未归类」处理：京外、
    正面——与展示层对 NULL 的兜底一致。
    """
    normalized_ids = [
        str(article_id).strip()
        for article_id in article_ids
        if str(article_id).strip()
    ]
    if not normalized_ids:
        return {}
    cur.execute(
        """
        SELECT article_id, is_beijing_related, sentiment_label
        FROM news_summaries
        WHERE article_id = ANY(%s)
        """,
        (normalized_ids,),
    )
    return {
        str(row["article_id"]): dict(row)
        for row in cur.fetchall()
    }


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
    {sentiment_expr} AS sentiment_label,
    ns.sentiment_confidence,
    {beijing_expr} AS is_beijing_related,
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
    owner_user_id: str,
    status: Optional[str] = None,
    only_ready: bool = False,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    report_type: Optional[str] = None,
    terms: Optional[Sequence[str]] = None,
    duty_unprocessed_only: bool = False,
    hour_from: Optional[int] = None,
    hour_to: Optional[int] = None,
    duplicate_state: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    decided_since: Optional[date] = None,
    batch_decided_at: Optional[datetime] = None,
) -> Tuple[List[str], List[Any]]:
    clauses: List[str] = ["mr.owner_user_id = %s"]
    params: List[Any] = [owner_user_id]
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
    beijing_expr, sentiment_expr = review_category_expressions("mr")
    if region in ("internal", "external"):
        clauses.append(f"({beijing_expr}) = %s")
        params.append(region == "internal")
    if sentiment in ("positive", "negative"):
        clauses.append(f"({sentiment_expr}) = %s")
        params.append(sentiment)
    # terms 已由 service 层切词去重；逐词一条 ILIKE（AND），按字面转义
    if terms:
        term_clauses, term_params = ilike_all_clauses(SEARCH_TEXT_EXPRESSION, terms)
        clauses.extend(term_clauses)
        params.extend(term_params)
    if duty_unprocessed_only:
        clauses.append(DUTY_UNPROCESSED_SQL)
    extra_clauses, extra_params = candidate_extra_filter_clauses(
        hour_from=hour_from,
        hour_to=hour_to,
        duplicate_state=duplicate_state,
        min_score=min_score,
        max_score=max_score,
    )
    clauses.extend(extra_clauses)
    params.extend(extra_params)
    decided_clauses, decided_params = decided_at_filter_clauses(
        column="mr.decided_at",
        decided_since=decided_since,
        batch_decided_at=batch_decided_at,
    )
    clauses.extend(decided_clauses)
    params.extend(decided_params)
    return clauses, params


def manual_review_max_rank(
    cur: psycopg.Cursor,
    status: str,
    *,
    owner_user_id: str,
    report_type: Optional[str] = None,
) -> float:
    type_expr = report_type_expr()
    normalized_report_type = normalize_report_type_value(report_type) or "zongbao"
    query = f"""
        SELECT COALESCE(MAX(rank), 0) AS max_rank
        FROM manual_reviews
        WHERE owner_user_id = %s
          AND status = %s
          AND {type_expr} = %s
    """
    cur.execute(query, (owner_user_id, status, normalized_report_type))
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
    "REVIEW_CATEGORY_OVERRIDE_KEYS",
    "SCORE_FEEDBACK_JOIN",
    "SEARCH_TEXT_EXPRESSION",
    "_build_manual_review_filters",
    "category_override_value",
    "fetch_news_summary_categories",
    "manual_review_max_rank",
    "normalize_report_type_value",
    "report_type_expr",
    "review_category_expressions",
]
