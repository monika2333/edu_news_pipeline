"""
manual_filter_service.py

Public facade for manual filter operations.
This module keeps legacy imports stable while delegating to focused sub-services.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, Sequence

from src.adapters.db_postgres_core import get_adapter

from . import manual_filter_action_service
from .manual_filter_action_service import (
    archive_items as _archive_items,
    bulk_decide as _bulk_decide,
    discard_candidates_before_date as _discard_candidates_before_date,
    reset_to_pending as _reset_to_pending,
    save_edits as _save_edits,
    update_ranks as _update_ranks,
)
from .manual_filter_cluster import DEFAULT_CLUSTER_THRESHOLD
from .manual_filter_helpers import DEFAULT_REPORT_TYPE, VALID_REPORT_TYPES
from .manual_filter_query_service import (
    list_candidates as _list_candidates,
    list_discarded as _list_discarded,
    list_review as _list_review,
    status_counts as _status_counts,
    trigger_clustering as _trigger_clustering,
)


def _sync_query_dependencies() -> None:
    from . import manual_filter_query_service

    manual_filter_query_service.get_adapter = get_adapter


def _sync_action_dependencies() -> None:
    manual_filter_action_service.get_adapter = get_adapter


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
    published_before: Optional[date] = None,
    view_mode: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, Any]:
    _sync_query_dependencies()
    return _list_candidates(
        limit=limit,
        offset=offset,
        region=region,
        sentiment=sentiment,
        cluster=cluster,
        cluster_threshold=cluster_threshold,
        force_refresh=force_refresh,
        q=q,
        published_before=published_before,
        view_mode=view_mode,
        report_type=report_type,
    )

def list_review(
    decision: str,
    *,
    limit: int = 30,
    offset: int = 0,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, Any]:
    _sync_query_dependencies()
    return _list_review(decision, limit=limit, offset=offset, report_type=report_type)


def list_discarded(*, limit: int = 30, offset: int = 0, report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, Any]:
    _sync_query_dependencies()
    return _list_discarded(limit=limit, offset=offset, report_type=report_type)


def status_counts(report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, int]:
    _sync_query_dependencies()
    return _status_counts(report_type=report_type)


def trigger_clustering(report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, Any]:
    _sync_query_dependencies()
    return _trigger_clustering(report_type=report_type)


def bulk_decide(
    *,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    pending_ids: Sequence[str] = (),
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, int]:
    return _bulk_decide(
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
    return _update_ranks(
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
    return _save_edits(edits, actor=actor, report_type=report_type)


def reset_to_pending(ids: Sequence[str], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    return _reset_to_pending(ids, actor=actor, report_type=report_type)


def archive_items(ids: Sequence[str], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    return _archive_items(ids, actor=actor, report_type=report_type)


def discard_candidates_before_date(
    *,
    region: str,
    sentiment: str,
    query: Optional[str] = None,
    published_before: Optional[date] = None,
    actor: Optional[str] = None,
    dry_run: bool = True,
) -> Dict[str, int]:
    _sync_action_dependencies()
    return _discard_candidates_before_date(
        region=region,
        sentiment=sentiment,
        query=query,
        published_before=published_before,
        actor=actor,
        dry_run=dry_run,
    )


__all__ = [
    "list_candidates",
    "list_review",
    "list_discarded",
    "discard_candidates_before_date",
    "status_counts",
    "trigger_clustering",
    "bulk_decide",
    "update_ranks",
    "save_edits",
    "reset_to_pending",
    "archive_items",
    "DEFAULT_REPORT_TYPE",
    "VALID_REPORT_TYPES",
    "DEFAULT_CLUSTER_THRESHOLD",
]
