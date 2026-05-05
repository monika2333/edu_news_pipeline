"""
manual_filter_query_service.py

Query-facing manual filter service helpers.
"""
from __future__ import annotations

from datetime import date
import logging
from typing import Any, Dict, List, Optional

from src.adapters.db_postgres_core import get_adapter

from .manual_filter_cluster import cluster_pending, refresh_clusters
from .manual_filter_helpers import DEFAULT_REPORT_TYPE, _normalize_report_type
from .manual_filter_serializers import FILTER_TAB_REPORT_TYPE, serialize_manual_filter_item

logger = logging.getLogger(__name__)


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
        items.append(
            serialize_manual_filter_item(
                dict(record),
                fallback_status=manual_status,
                report_type=target_report_type,
            )
        )
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _list_candidate_search(
    *,
    limit: int,
    offset: int,
    region: Optional[str],
    sentiment: Optional[str],
    query: Optional[str],
    published_before: Optional[date],
    report_type: str,
) -> Dict[str, Any]:
    adapter = get_adapter()
    rows, total = adapter.search_manual_candidates(  # type: ignore[attr-defined]
        query=query,
        published_before=published_before,
        limit=limit,
        offset=offset,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
    )
    items = [
        serialize_manual_filter_item(
            dict(record),
            fallback_status="pending",
            report_type=report_type,
        )
        for record in rows
    ]
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "view_mode": "search",
    }


def _list_candidate_browse(
    *,
    limit: int,
    offset: int,
    region: Optional[str],
    sentiment: Optional[str],
    cluster: bool,
    cluster_threshold: Optional[float],
    force_refresh: bool,
    report_type: str,
) -> Dict[str, Any]:
    if cluster:
        return cluster_pending(
            region=region,
            sentiment=sentiment,
            limit=limit,
            offset=offset,
            cluster_threshold=cluster_threshold,
            force_refresh=force_refresh,
            report_type=report_type,
        )
    result = _paginate_by_status(
        "pending",
        limit=limit,
        offset=offset,
        only_ready=True,
        region=region,
        sentiment=sentiment,
        report_type=report_type,
    )
    result["view_mode"] = "browse"
    return result


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
    region = region if region in ("internal", "external") else None
    sentiment = sentiment if sentiment in ("positive", "negative") else None
    target_report_type = FILTER_TAB_REPORT_TYPE
    normalized_query = (q or "").strip() or None
    search_mode = (view_mode or "").strip().lower() == "search" or normalized_query is not None or published_before is not None
    logger.info(
        "Listing candidates: limit=%s offset=%s region=%s sentiment=%s report_type=%s view_mode=%s",
        limit,
        offset,
        region,
        sentiment,
        target_report_type,
        "search" if search_mode else "browse",
    )
    if search_mode:
        return _list_candidate_search(
            region=region,
            sentiment=sentiment,
            limit=limit,
            offset=offset,
            query=normalized_query,
            published_before=published_before,
            report_type=target_report_type,
        )
    return _list_candidate_browse(
        limit=limit,
        offset=offset,
        region=region,
        sentiment=sentiment,
        cluster=cluster,
        cluster_threshold=cluster_threshold,
        force_refresh=force_refresh,
        report_type=target_report_type,
    )


def list_review(decision: str, *, limit: int = 30, offset: int = 0, report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, Any]:
    decision = decision if decision in ("selected", "backup") else "selected"
    target_report_type = _normalize_report_type(report_type)
    logger.info("Listing review items: decision=%s limit=%s offset=%s report_type=%s", decision, limit, offset, target_report_type)
    return _paginate_by_status(decision, limit=limit, offset=offset, only_ready=False, report_type=target_report_type)


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


def status_counts(report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, int]:
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    return adapter.manual_review_status_counts(report_type=target_report_type)  # type: ignore[attr-defined]


def trigger_clustering(report_type: str = DEFAULT_REPORT_TYPE) -> Dict[str, Any]:
    target_report_type = _normalize_report_type(report_type)
    refreshed = refresh_clusters(report_type=target_report_type)
    return {"refreshed": refreshed}


__all__ = [
    "list_candidates",
    "list_review",
    "list_discarded",
    "status_counts",
    "trigger_clustering",
]
