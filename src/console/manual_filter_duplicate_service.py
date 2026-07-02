from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

import requests

from src.adapters.llm_chat import LLMQuotaError
from src.adapters.llm_duplicate_review import (
    DuplicateReviewResponseError,
    call_duplicate_review,
)
from src.config import get_settings

from .manual_filter_helpers import _normalize_report_type
from .manual_filter_query_service import list_review

logger = logging.getLogger(__name__)

MAX_DUPLICATE_REVIEW_ITEMS = 200
VALID_REVIEW_DECISIONS = {"selected", "backup"}


class DuplicateReviewError(RuntimeError):
    """Base error for the interactive duplicate-review workflow."""


class DuplicateReviewLimitError(DuplicateReviewError):
    """Raised when a review column exceeds the supported item limit."""


class DuplicateReviewTimeoutError(DuplicateReviewError):
    """Raised when the duplicate-review model times out."""


class DuplicateReviewUnavailableError(DuplicateReviewError):
    """Raised when the configured model service is unavailable."""


class DuplicateReviewInvalidResponseError(DuplicateReviewError):
    """Raised when the model response cannot be parsed safely."""


def _model_input_item(item: Mapping[str, Any]) -> dict[str, str]:
    return {
        "article_id": str(item.get("article_id") or ""),
        "title": str(item.get("title") or ""),
        "summary": str(item.get("summary") or ""),
        "source": str(item.get("llm_source_display") or item.get("source") or ""),
    }


def _merge_duplicate_groups(
    groups: Sequence[Sequence[str]],
    *,
    allowed_ids: set[str],
    article_order: Sequence[str],
) -> list[list[str]]:
    merged_sets: list[set[str]] = []
    for raw_group in groups:
        valid_group = {article_id for article_id in raw_group if article_id in allowed_ids}
        if len(valid_group) < 2:
            continue
        overlapping = [group for group in merged_sets if group & valid_group]
        if overlapping:
            for group in overlapping:
                valid_group.update(group)
                merged_sets.remove(group)
        merged_sets.append(valid_group)

    order_index = {article_id: index for index, article_id in enumerate(article_order)}
    normalized = [
        sorted(group, key=lambda article_id: order_index[article_id])
        for group in merged_sets
        if len(group) >= 2
    ]
    return sorted(normalized, key=lambda group: order_index[group[0]])


def _response_item(item: Mapping[str, Any]) -> dict[str, Any]:
    score = item.get("external_importance_score")
    if score is None:
        score = item.get("score")
    return {
        "article_id": item.get("article_id"),
        "title": item.get("title") or "",
        "summary": item.get("summary") or "",
        "source": item.get("llm_source_display") or item.get("source") or "",
        "url": item.get("url") or "",
        "status": item.get("manual_status") or item.get("status") or "",
        "report_type": item.get("report_type") or "",
        "score": score,
        "bonus_keywords": item.get("bonus_keywords") or [],
    }


def _call_model(items: Sequence[Mapping[str, str]]) -> list[list[str]]:
    try:
        return call_duplicate_review(items)
    except requests.Timeout as exc:
        raise DuplicateReviewTimeoutError("AI 查重请求超时，请稍后重试") from exc
    except LLMQuotaError as exc:
        raise DuplicateReviewUnavailableError("AI 模型额度不足，暂时无法查重") from exc
    except DuplicateReviewResponseError as exc:
        raise DuplicateReviewInvalidResponseError("AI 返回格式错误，请重新检查") from exc
    except requests.RequestException as exc:
        raise DuplicateReviewUnavailableError("AI 查重服务暂时不可用") from exc
    except RuntimeError as exc:
        raise DuplicateReviewUnavailableError("AI 查重配置不可用") from exc


def check_duplicates(*, report_type: str, decision: str) -> dict[str, Any]:
    target_report_type = _normalize_report_type(report_type)
    target_decision = decision if decision in VALID_REVIEW_DECISIONS else "selected"
    review = list_review(
        target_decision,
        limit=MAX_DUPLICATE_REVIEW_ITEMS,
        offset=0,
        report_type=target_report_type,
    )
    total = int(review.get("total") or 0)
    if total > MAX_DUPLICATE_REVIEW_ITEMS:
        raise DuplicateReviewLimitError(
            f"当前栏目有 {total} 条新闻，超过单次查重上限 {MAX_DUPLICATE_REVIEW_ITEMS} 条"
        )

    items = list(review.get("items") or [])
    settings = get_settings()
    if len(items) < 2:
        return {
            "checked_count": len(items),
            "model": settings.llm_scoring_model,
            "groups": [],
        }

    model_items = [_model_input_item(item) for item in items]
    raw_groups = _call_model(model_items)
    article_order = [item["article_id"] for item in model_items if item["article_id"]]
    groups = _merge_duplicate_groups(
        raw_groups,
        allowed_ids=set(article_order),
        article_order=article_order,
    )
    item_lookup = {str(item.get("article_id")): item for item in items}
    response_groups = [
        {
            "group_id": f"duplicate-{index}",
            "items": [_response_item(item_lookup[article_id]) for article_id in group],
        }
        for index, group in enumerate(groups, start=1)
    ]
    logger.info(
        "Checked review duplicates: report_type=%s decision=%s checked=%s groups=%s",
        target_report_type,
        target_decision,
        len(items),
        len(response_groups),
    )
    return {
        "checked_count": len(items),
        "model": settings.llm_scoring_model,
        "groups": response_groups,
    }


__all__ = [
    "DuplicateReviewError",
    "DuplicateReviewInvalidResponseError",
    "DuplicateReviewLimitError",
    "DuplicateReviewTimeoutError",
    "DuplicateReviewUnavailableError",
    "MAX_DUPLICATE_REVIEW_ITEMS",
    "check_duplicates",
]
