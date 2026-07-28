"""
manual_filter_serializers.py

Shared serializer helpers for manual filter records.
"""
from __future__ import annotations

from typing import Any, Dict

from .manual_filter_helpers import (
    DEFAULT_REPORT_TYPE,
    _attach_group_fields,
    _attach_source_fields,
    _bonus_keywords,
)


FILTER_TAB_REPORT_TYPE = DEFAULT_REPORT_TYPE


def serialize_manual_filter_item(
    record: Dict[str, Any],
    *,
    fallback_status: str,
    report_type: str,
) -> Dict[str, Any]:
    item = _attach_group_fields(_attach_source_fields(dict(record)))
    item.pop("content_markdown", None)
    item["manual_status"] = item.get("status") or fallback_status
    item["summary"] = item.get("manual_summary") or item.get("llm_summary") or ""
    item["bonus_keywords"] = _bonus_keywords(item.get("score_details"))
    item["report_type"] = item.get("report_type") or report_type
    feedback_type = item.pop("score_feedback_type", None)
    feedback_score = item.pop("score_feedback_score_value", None)
    feedback_notes = item.pop("score_feedback_notes", None)
    feedback_submitted_by = item.pop("score_feedback_submitted_by", None)
    feedback_submitted_by_user_id = item.pop(
        "score_feedback_submitted_by_user_id",
        None,
    )
    feedback_submitted_by_display_name = item.pop(
        "score_feedback_submitted_by_display_name",
        None,
    )
    feedback_updated_at = item.pop("score_feedback_updated_at", None)
    item["score_feedback"] = (
        {
            "feedback_type": feedback_type,
            "score_value": feedback_score,
            "notes": feedback_notes,
            "submitted_by": feedback_submitted_by,
            "submitted_by_user_id": feedback_submitted_by_user_id,
            "submitted_by_display_name": feedback_submitted_by_display_name,
            "updated_at": feedback_updated_at,
        }
        if feedback_type
        else None
    )
    return item


__all__ = ["FILTER_TAB_REPORT_TYPE", "serialize_manual_filter_item"]
