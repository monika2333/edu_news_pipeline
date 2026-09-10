"""
manual_filter_service.py

Read-only query entry point for manual filter operations.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from .manual_filter_cluster import DEFAULT_CLUSTER_THRESHOLD
from .manual_filter_duplicate_service import check_duplicates as _check_duplicates
from .manual_filter_helpers import DEFAULT_REPORT_TYPE, VALID_REPORT_TYPES
from .manual_filter_query_service import (
    list_candidates as _list_candidates,
    list_discarded as _list_discarded,
    list_review as _list_review,
    status_counts as _status_counts,
    trigger_clustering as _trigger_clustering,
)

def list_candidates(
    *,
    limit: int = 30,
    offset: int = 0,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    cluster: bool = False,
    cluster_threshold: Optional[float] = None,
    force_refresh: bool = False,
    q: Optional[str] = None,
    created_before: Optional[date] = None,
    view_mode: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
    duty_unprocessed_only: bool = False,
) -> Dict[str, Any]:
    return _list_candidates(
        limit=limit,
        offset=offset,
        region=region,
        sentiment=sentiment,
        cluster=cluster,
        cluster_threshold=cluster_threshold,
        force_refresh=force_refresh,
        q=q,
        created_before=created_before,
        view_mode=view_mode,
        report_type=report_type,
        duty_unprocessed_only=duty_unprocessed_only,
    )

def list_review(
    decision: str,
    *,
    limit: int = 30,
    offset: int = 0,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, Any]:
    return _list_review(decision, limit=limit, offset=offset, report_type=report_type)


def list_discarded(
    *,
    limit: int = 30,
    offset: int = 0,
    report_type: str = DEFAULT_REPORT_TYPE,
    q: Optional[str] = None,
) -> Dict[str, Any]:
    return _list_discarded(
        limit=limit,
        offset=offset,
        report_type=report_type,
        q=q,
    )


def status_counts(report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, int]:
    return _status_counts(report_type=report_type)


def trigger_clustering() -> Dict[str, Any]:
    return _trigger_clustering()


def check_duplicates(*, report_type: str, decision: str) -> Dict[str, Any]:
    return _check_duplicates(report_type=report_type, decision=decision)


__all__ = [
    "list_candidates",
    "list_review",
    "list_discarded",
    "status_counts",
    "trigger_clustering",
    "check_duplicates",
    "DEFAULT_REPORT_TYPE",
    "VALID_REPORT_TYPES",
    "DEFAULT_CLUSTER_THRESHOLD",
]
