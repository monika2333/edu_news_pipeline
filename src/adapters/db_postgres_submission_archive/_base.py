from __future__ import annotations

from typing import Any, Literal, Optional, TypedDict


PRIOR_MATCH_REPORT_TYPES = frozenset({"feedback"})


class ManualLinkMutationResult(TypedDict):
    state: Literal[
        "updated",
        "not_found",
        "processing",
        "article_not_found",
    ]
    item: Optional[dict[str, Any]]


class ItemFieldUpdateResult(TypedDict):
    state: Literal[
        "updated",
        "not_found",
        "processing",
    ]
    item: Optional[dict[str, Any]]


class PriorMatchDecisionMutationResult(TypedDict):
    state: Literal["updated", "not_found", "not_decidable"]
    prior_match: Optional[dict[str, Any]]


# 条目对外返回字段：排除 embedding（bytea 无法 JSON 序列化）与归一化内部字段。
_ITEM_PUBLIC_COLUMNS = """
    i.id,
    i.report_id,
    i.section,
    i.marker,
    i.order_index,
    i.title,
    i.body,
    i.source,
    i.urls,
    i.article_id,
    i.link_status,
    i.link_title_score,
    i.link_body_score,
    i.link_combined_score,
    i.best_candidate_article_id,
    i.link_matched_at,
    i.created_at
"""

__all__ = [
    "PRIOR_MATCH_REPORT_TYPES",
    "ItemFieldUpdateResult",
    "ManualLinkMutationResult",
    "PriorMatchDecisionMutationResult",
]
