"""Shared SQL fragment builders for whitespace-separated keyword search.

切词发生在调用方（console service 层用 ``src.console.search_terms`` 完成），
adapter 收到的是去重后的词列表；本模块只负责把词列表翻译成 ILIKE 子句，
并保证每个词按字面匹配（转义 ``%``、``_``、``\\``）。
"""
from __future__ import annotations

from typing import Any, List, Sequence, Tuple


def escape_like(term: str) -> str:
    # LIKE 的默认转义符是反斜杠，先转义它本身再转义两个通配符，保证按字面匹配
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def ilike_all_clauses(
    expression: str,
    terms: Sequence[str],
) -> Tuple[List[str], List[Any]]:
    """逐词 ILIKE 子句与参数，词与词之间 AND（由调用方 join clauses）。

    每个词一条独立 ILIKE——这是 trigram GIN 索引能生效的唯一形态；
    空词列表返回空子句组，调用方据此跳过检索条件。
    """
    clauses = [f"{expression} ILIKE %s" for _ in terms]
    params: List[Any] = [f"%{escape_like(term)}%" for term in terms]
    return clauses, params


def ilike_any_clause(
    expression: str,
    terms: Sequence[str],
) -> Tuple[str, List[Any]]:
    """逐词 ILIKE 的 OR 组（已整体括号），任一词命中即成立。"""
    clauses, params = ilike_all_clauses(expression, terms)
    return f"({' OR '.join(clauses)})", params


__all__ = [
    "escape_like",
    "ilike_all_clauses",
    "ilike_any_clause",
]
