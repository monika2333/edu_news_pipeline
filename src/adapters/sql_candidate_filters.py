"""Shared WHERE-clause builder for the manual-filter workspace refinement filters.

时段（收录小时）、报送重复标签、外部重要性分数三个筛选在管理员候选列表与
值班候选列表中语义一致；两条查询链都把 news_summaries 别名为 ``ns``，
因此子句统一按 ``ns`` 书写。归一化（类型校验、范围检查）由 console 层的
``normalize_candidate_refine_filters`` 负责，这里只接收已合法的值。
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

# 与 fetch_duplicate_badges 的徽章口径一致：非 dismissed 的命中即视为带标签，
# confirmed/suspected 都算「已报送/疑似已报送」。
DUPLICATE_TAGGED_SQL = (
    "EXISTS ("
    "SELECT 1 FROM submission_duplicate_matches sdm "
    "WHERE sdm.article_id = ns.article_id AND sdm.state <> 'dismissed')"
)


def candidate_created_hour_expr() -> str:
    """收录时间的小时（0-23），与 CREATED_LOCAL_DATE_EXPRESSION 同用上海时区。"""
    return "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai')"


def candidate_extra_filter_clauses(
    *,
    hour_from: Optional[int] = None,
    hour_to: Optional[int] = None,
    duplicate_state: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> Tuple[List[str], List[Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if hour_from is not None and hour_to is not None and hour_from == hour_to:
        clauses.append(f"{candidate_created_hour_expr()} = %s")
        params.append(hour_from)
    elif hour_from is not None and hour_to is not None and hour_from > hour_to:
        # 跨零点区间（如 22 时到次日 6 时）按钟面回绕解释
        clauses.append(
            f"({candidate_created_hour_expr()} >= %s OR {candidate_created_hour_expr()} <= %s)"
        )
        params.extend([hour_from, hour_to])
    else:
        if hour_from is not None:
            clauses.append(f"{candidate_created_hour_expr()} >= %s")
            params.append(hour_from)
        if hour_to is not None:
            clauses.append(f"{candidate_created_hour_expr()} <= %s")
            params.append(hour_to)
    if duplicate_state == "untagged":
        clauses.append(f"NOT {DUPLICATE_TAGGED_SQL}")
    elif duplicate_state == "tagged":
        clauses.append(DUPLICATE_TAGGED_SQL)
    if min_score is not None:
        clauses.append("ns.external_importance_score >= %s")
        params.append(min_score)
    if max_score is not None:
        clauses.append("ns.external_importance_score <= %s")
        params.append(max_score)
    return clauses, params


__all__ = [
    "candidate_created_hour_expr",
    "candidate_extra_filter_clauses",
    "DUPLICATE_TAGGED_SQL",
]
