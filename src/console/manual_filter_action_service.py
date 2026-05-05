"""
manual_filter_action_service.py

Action-facing manual filter service helpers.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter

from . import manual_filter_decisions
from .manual_filter_helpers import DEFAULT_REPORT_TYPE
from .manual_filter_serializers import FILTER_TAB_REPORT_TYPE


def bulk_decide(
    *,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    pending_ids: Sequence[str] = (),
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, int]:
    return manual_filter_decisions.bulk_decide(
        selected_ids=selected_ids,
        backup_ids=backup_ids,
        discarded_ids=discarded_ids,
        pending_ids=pending_ids,
        actor=actor,
        report_type=report_type,
    )


def update_ranks(
    *,
    selected_order: Sequence[str],
    backup_order: Sequence[str],
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, int]:
    return manual_filter_decisions.update_ranks(
        selected_order=selected_order,
        backup_order=backup_order,
        actor=actor,
        report_type=report_type,
    )


def save_edits(
    edits: Dict[str, Dict[str, Any]],
    *,
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> int:
    return manual_filter_decisions.save_edits(edits, actor=actor, report_type=report_type)


def reset_to_pending(ids: Sequence[str], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    return manual_filter_decisions.reset_to_pending(ids, actor=actor, report_type=report_type)


def archive_items(ids: Sequence[str], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    return manual_filter_decisions.archive_items(ids, actor=actor, report_type=report_type)


def discard_candidates_before_date(
    *,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    published_before: Optional[date] = None,
    actor: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, int]:
    normalized_region = region if region in ("internal", "external") else None
    normalized_sentiment = sentiment if sentiment in ("positive", "negative") else None
    if normalized_region is None or normalized_sentiment is None:
        raise ValueError("discard_before_date requires explicit filter bucket")
    adapter = get_adapter()
    matched = adapter.count_manual_candidates_before_date(  # type: ignore[attr-defined]
        region=normalized_region,
        sentiment=normalized_sentiment,
        query=(query or "").strip() or None,
        published_before=published_before,
        report_type=FILTER_TAB_REPORT_TYPE,
    )
    if dry_run or matched <= 0:
        return {"matched": matched, "updated": 0}
    updated = adapter.discard_manual_candidates_before_date(  # type: ignore[attr-defined]
        region=normalized_region,
        sentiment=normalized_sentiment,
        query=(query or "").strip() or None,
        published_before=published_before,
        actor=actor,
        report_type=FILTER_TAB_REPORT_TYPE,
    )
    return {"matched": matched, "updated": updated}


__all__ = [
    "bulk_decide",
    "update_ranks",
    "save_edits",
    "reset_to_pending",
    "archive_items",
    "discard_candidates_before_date",
]
