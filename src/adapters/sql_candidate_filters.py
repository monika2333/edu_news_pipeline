"""Shared WHERE-clause builder for the manual-filter workspace refinement filters.

时段（收录小时）、报送重复标签、外部重要性分数三个筛选在管理员候选列表与
值班候选列表中语义一致；两条查询链都把 news_summaries 别名为 ``ns``，
因此子句统一按 ``ns`` 书写。归一化（类型校验、范围检查）由 console 层的
``normalize_candidate_refine_filters`` 负责，这里只接收已合法的值。
"""
from __future__ import annotations

from datetime import date, datetime
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


def decided_at_filter_clauses(
    *,
    column: str,
    decided_since: Optional[date] = None,
    batch_decided_at: Optional[datetime] = None,
) -> Tuple[List[str], List[Any]]:
    """放弃时间（decided_at）筛选子句，管理员与值班两条查询链共用。

    ``column`` 是调用方提供的固定 SQL 片段（如 ``mr.decided_at`` /
    ``sr.decided_at``），绝不来自用户输入。口径与「收录时间」一致：换算成
    Asia/Shanghai 本地日期后比较。``decided_at`` 为空的行在两种条件下都不命中
    （与 NULL 比较结果为 NULL，不满足 WHERE）。
    """
    clauses: List[str] = []
    params: List[Any] = []
    if decided_since is not None:
        clauses.append(f"({column} AT TIME ZONE 'Asia/Shanghai')::date >= %s")
        params.append(decided_since)
    if batch_decided_at is not None:
        # 「同一批」定位依赖：同一次批量操作写入的 decided_at 精确相等
        clauses.append(f"{column} = %s")
        params.append(batch_decided_at)
    return clauses, params


__all__ = [
    "candidate_created_hour_expr",
    "candidate_extra_filter_clauses",
    "decided_at_filter_clauses",
    "DUPLICATE_TAGGED_SQL",
]
