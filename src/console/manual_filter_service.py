"""
manual_filter_service.py

Public facade for manual filter operations.
This module re-exports functions from sub-modules and provides list/filter APIs.
All existing imports should continue to work as before.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.adapters.db_postgres_core import get_adapter

# ─────────────────────────────────────────────────────────────────────────────
# Re-export constants from helpers
# ─────────────────────────────────────────────────────────────────────────────
from .manual_filter_helpers import (
    DEFAULT_REPORT_TYPE,
    VALID_REPORT_TYPES,
    _attach_group_fields,
    _attach_source_fields,
    _bonus_keywords,
    _normalize_report_type,
)

# ─────────────────────────────────────────────────────────────────────────────
# Re-export cluster functions
# ─────────────────────────────────────────────────────────────────────────────
from .manual_filter_cluster import (
    DEFAULT_CLUSTER_THRESHOLD,
    _candidate_rank_key_by_record,
    _paginate_clusters,
    cluster_pending,
    refresh_clusters,
)

# ─────────────────────────────────────────────────────────────────────────────
# Re-export decision functions
# ─────────────────────────────────────────────────────────────────────────────
from .manual_filter_decisions import (
    _apply_decision,
    _apply_ranked_decision,
    _next_rank,
    archive_items,
    bulk_decide,
    reset_to_pending,
    save_edits,
    update_ranks,
)


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Paginate by status (internal helper for list APIs)
# ─────────────────────────────────────────────────────────────────────────────
def _paginate_by_status(
    manual_status: str,
    *,
    limit: int,
    offset: int,
    only_ready: bool = False,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
    order_by_decided_at: bool = False,
) -> Dict[str, Any]:
    adapter = get_adapter()
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    target_report_type = _normalize_report_type(report_type)
    rows, total = adapter.fetch_manual_reviews(  # type: ignore[attr-defined]
        status=manual_status,
        limit=limit,
        offset=offset,
        only_ready=only_ready,
        region=region,
        sentiment=sentiment,
        report_type=target_report_type,
        order_by_decided_at=order_by_decided_at,
    )
    items: List[Dict[str, Any]] = []
    for record in rows:
        record = _attach_group_fields(_attach_source_fields(dict(record)))
        record["manual_status"] = record.get("status") or manual_status
        record["summary"] = record.get("manual_summary") or record.get("llm_summary") or ""
        record["bonus_keywords"] = _bonus_keywords(record.get("score_details"))
        record["report_type"] = record.get("report_type") or target_report_type
        items.append(record)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ─────────────────────────────────────────────────────────────────────────────
# List candidates (pending items, with optional clustering)
# ─────────────────────────────────────────────────────────────────────────────
def list_candidates(
    *,
    limit: int = 30,
    offset: int = 0,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    cluster: bool = False,
    cluster_threshold: Optional[float] = None,
    force_refresh: bool = False,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, Any]:
    region = region if region in ("internal", "external") else None
    sentiment = sentiment if sentiment in ("positive", "negative") else None
    target_report_type = _normalize_report_type(report_type)
    logger.info(
        "Listing candidates: limit=%s offset=%s region=%s sentiment=%s report_type=%s",
        limit,
        offset,
        region,
        sentiment,
        target_report_type,
    )
    if cluster:
        return cluster_pending(
            region=region,
            sentiment=sentiment,
            limit=limit,
            offset=offset,
            cluster_threshold=cluster_threshold,
            force_refresh=force_refresh,
            report_type=target_report_type,
        )
    return _paginate_by_status(
        "pending",
        limit=limit,
        offset=offset,
        only_ready=True,
        region=region,
        sentiment=sentiment,
        report_type=target_report_type,
    )


# ─────────────────────────────────────────────────────────────────────────────
# List review items (selected or backup)
# ─────────────────────────────────────────────────────────────────────────────
def list_review(decision: str, *, limit: int = 30, offset: int = 0, report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, Any]:
    decision = decision if decision in ("selected", "backup") else "selected"
    target_report_type = _normalize_report_type(report_type)
    logger.info("Listing review items: decision=%s limit=%s offset=%s report_type=%s", decision, limit, offset, target_report_type)
    return _paginate_by_status(decision, limit=limit, offset=offset, only_ready=False, report_type=target_report_type)


# ─────────────────────────────────────────────────────────────────────────────
# List discarded items
# ─────────────────────────────────────────────────────────────────────────────
def list_discarded(*, limit: int = 30, offset: int = 0, report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, Any]:
    target_report_type = _normalize_report_type(report_type)
    logger.info("Listing discarded items: limit=%s offset=%s report_type=%s", limit, offset, target_report_type)
    return _paginate_by_status(
        "discarded",
        limit=limit,
        offset=offset,
        only_ready=False,
        report_type=target_report_type,
        order_by_decided_at=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Status counts
# ─────────────────────────────────────────────────────────────────────────────
def status_counts(report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, int]:
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    return adapter.manual_review_status_counts(report_type=target_report_type)  # type: ignore[attr-defined]


def trigger_clustering(report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, Any]:
    target_report_type = _normalize_report_type(report_type)
    refreshed = refresh_clusters(report_type=target_report_type)
    return {"refreshed": refreshed}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
__all__ = [
    # List APIs
    "list_candidates",
    "list_review",
    "list_discarded",
    "status_counts",
    "trigger_clustering",
    # Decision APIs
    "bulk_decide",
    "update_ranks",
    "save_edits",
    "reset_to_pending",
    # Archive APIs
    "archive_items",
    # Constants (for backward compatibility)
    "DEFAULT_REPORT_TYPE",
    "VALID_REPORT_TYPES",
    "DEFAULT_CLUSTER_THRESHOLD",
]
