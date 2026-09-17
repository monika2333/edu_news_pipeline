from __future__ import annotations

MAX_SEARCH_TERMS = 10


class TooManySearchTermsError(ValueError):
    """检索词去重后超过共享上限；路由层把它当作普通 ValueError 转成 422。"""


def normalize_search_terms(query: str) -> list[str]:
    """把原始检索串切成用于 AND 检索的词列表。

    - 任意 Unicode 空白（半角/全角空格、Tab 等）都是分隔符，连续空白合并；
    - 大小写不敏感去重，保留首次出现的写法与顺序；
    - 全空白返回空列表，由各 service 按自己的空值契约处理（文章检索报
      422，存档检索返回空结果）；
    - 去重后超过 MAX_SEARCH_TERMS 个词时抛 TooManySearchTermsError，
      上限是为了约束 SQL 条件数量，不是业务规则。
    """
    terms: list[str] = []
    seen: set[str] = set()
    for token in str(query or "").split():
        folded = token.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        terms.append(token)
        if len(terms) > MAX_SEARCH_TERMS:
            raise TooManySearchTermsError(
                f"检索词最多 {MAX_SEARCH_TERMS} 个，请减少关键词后重试"
            )
    return terms


__all__ = [
    "MAX_SEARCH_TERMS",
    "TooManySearchTermsError",
    "normalize_search_terms",
]
