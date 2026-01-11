"""
manual_filter_cluster.py

Clustering logic and in-memory cache for grouping pending articles by title similarity.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.adapters.db_postgres_core import get_adapter
from src.adapters.title_cluster import cluster_titles

from .manual_filter_helpers import (
    DEFAULT_REPORT_TYPE,
    _attach_group_fields,
    _attach_source_fields,
    _bonus_keywords,
    _normalize_report_type,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_CLUSTER_THRESHOLD = 0.9
MANUAL_CLUSTER_LOCK_ID = 9001001
CLUSTER_BUCKET_KEYS = (
    "internal_positive",
    "internal_negative",
    "external_positive",
    "external_negative",
)


# ─────────────────────────────────────────────────────────────────────────────
# Rank key for sorting
# ─────────────────────────────────────────────────────────────────────────────
def _candidate_rank_key_by_record(record: Dict[str, Any]) -> Tuple[float, float, float, float]:
    def _num(val: Any, default: float = float("-inf")) -> float:
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def _ts(val: Any) -> float:
        if val is None:
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return datetime.fromisoformat(str(val)).timestamp()
        except Exception:
            try:
                return float(val)
            except Exception:
                return 0.0

    ext_score = _num(record.get("external_importance_score"))
    manual_rank = _num(record.get("manual_rank"))
    score = _num(record.get("score"))
    ts_val = _ts(record.get("publish_time_iso") or record.get("publish_time"))
    return (ext_score, manual_rank, score, ts_val)


def _bucket_key_from_filters(region: Optional[str], sentiment: Optional[str]) -> Optional[str]:
    if region in ("internal", "external") and sentiment in ("positive", "negative"):
        return f"{region}_{sentiment}"
    return None


def _bucket_key_for_record(record: Dict[str, Any]) -> str:
    region = "internal" if record.get("is_beijing_related") else "external"
    sentiment = "negative" if (record.get("sentiment_label") or "").lower() == "negative" else "positive"
    return f"{region}_{sentiment}"


def refresh_clusters(
    *,
    cluster_threshold: Optional[float] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> bool:
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    try:
        threshold_val = float(cluster_threshold) if cluster_threshold is not None else DEFAULT_CLUSTER_THRESHOLD
    except Exception:
        threshold_val = DEFAULT_CLUSTER_THRESHOLD
    threshold_val = max(0.0, min(threshold_val, 1.0))

    if not adapter.try_advisory_lock(MANUAL_CLUSTER_LOCK_ID):
        return False

    try:
        records = _collect_pending(None, None, fetch_limit=5000, adapter=adapter, report_type=target_report_type)
        buckets: Dict[str, List[Dict[str, Any]]] = {key: [] for key in CLUSTER_BUCKET_KEYS}
        for record in records:
            bucket_key = _bucket_key_for_record(record)
            buckets.setdefault(bucket_key, []).append(record)

        clusters: List[Dict[str, Any]] = []
        for bucket_key, items in buckets.items():
            if not items:
                continue
            items_sorted = sorted(items, key=_candidate_rank_key_by_record, reverse=True)
            titles = [item.get("title") or "" for item in items_sorted]
            groups = cluster_titles(titles, threshold=threshold_val) or [list(range(len(items_sorted)))]

            for idx, group in enumerate(groups):
                group_items = [items_sorted[i] for i in group if 0 <= i < len(items_sorted)]
                if not group_items:
                    continue
                group_items.sort(key=_candidate_rank_key_by_record, reverse=True)
                clusters.append(
                    {
                        "cluster_id": f"{bucket_key}-{idx}",
                        "bucket_key": bucket_key,
                        "item_ids": [item["article_id"] for item in group_items if item.get("article_id")],
                        "report_type": target_report_type,
                    }
                )

        adapter.replace_manual_clusters(clusters, report_type=target_report_type)  # type: ignore[attr-defined]
        return True
    finally:
        adapter.release_advisory_lock(MANUAL_CLUSTER_LOCK_ID)

# ─────────────────────────────────────────────────────────────────────────────
# Collect pending items for clustering
# ─────────────────────────────────────────────────────────────────────────────
def _collect_pending(
    region: Optional[str],
    sentiment: Optional[str],
    fetch_limit: int = 5000,
    *,
    adapter: Any = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> List[Dict[str, Any]]:
    adapter = adapter or get_adapter()
    target_report_type = _normalize_report_type(report_type)
    rows = adapter.fetch_manual_pending_for_cluster(  # type: ignore[attr-defined]
        region=region,
        sentiment=sentiment,
        fetch_limit=fetch_limit,
        report_type=target_report_type,
    )
    records: List[Dict[str, Any]] = []
    for row in rows:
        record = _attach_group_fields(_attach_source_fields(dict(row)))
        record["summary"] = record.get("manual_summary") or record.get("llm_summary") or ""
        record["bonus_keywords"] = _bonus_keywords(record.get("score_details"))
        record["external_importance_score"] = record.get("external_importance_score")
        record["report_type"] = record.get("report_type") or target_report_type
        records.append(record)
    return records


# ─────────────────────────────────────────────────────────────────────────────
# Cluster pending entries
# ─────────────────────────────────────────────────────────────────────────────

def cluster_pending(
    *,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    cluster_threshold: Optional[float] = None,
    force_refresh: bool = False,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, Any]:
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    try:
        threshold_val = float(cluster_threshold) if cluster_threshold is not None else DEFAULT_CLUSTER_THRESHOLD
    except Exception:
        threshold_val = DEFAULT_CLUSTER_THRESHOLD
    threshold_val = max(0.0, min(threshold_val, 1.0))

    if force_refresh:
        refresh_clusters(cluster_threshold=threshold_val, report_type=target_report_type)

    bucket_key = _bucket_key_from_filters(region, sentiment)
    rows = adapter.fetch_manual_clusters(bucket_key=bucket_key, report_type=target_report_type)  # type: ignore[attr-defined]
    if not rows:
        return {"clusters": [], "total": 0}

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        record = _attach_source_fields(dict(row))
        record["summary"] = record.get("manual_summary") or record.get("llm_summary") or ""
        record["bonus_keywords"] = _bonus_keywords(record.get("score_details"))
        record["report_type"] = target_report_type
        cluster_id = record.get("cluster_id")
        bucket = record.get("bucket_key")
        if not cluster_id or not bucket:
            continue
        cluster = grouped.setdefault(
            cluster_id,
            {
                "cluster_id": cluster_id,
                "report_type": target_report_type,
                "bucket_key": bucket,
                "items": [],
            },
        )
        cluster["items"].append(
            {
                "article_id": record.get("article_id"),
                "title": record.get("title"),
                "summary": record.get("summary"),
                "source": record.get("source"),
                "url": record.get("url"),
                "score": record.get("score"),
                "external_importance_score": record.get("external_importance_score"),
                "sentiment_label": record.get("sentiment_label"),
                "is_beijing_related": record.get("is_beijing_related"),
                "llm_source_display": record.get("llm_source_display"),
                "llm_source_raw": record.get("llm_source_raw"),
                "llm_source_manual": record.get("llm_source_manual"),
                "bonus_keywords": record.get("bonus_keywords"),
                "manual_rank": record.get("manual_rank"),
                "publish_time": record.get("publish_time"),
                "publish_time_iso": record.get("publish_time_iso"),
            }
        )

    clusters: List[Dict[str, Any]] = []
    for cluster in grouped.values():
        items = cluster.get("items") or []
        if not items:
            continue
        items.sort(key=_candidate_rank_key_by_record, reverse=True)
        for item in items:
            item.pop("manual_rank", None)
            item.pop("publish_time", None)
            item.pop("publish_time_iso", None)
        rep = items[0]
        cluster["size"] = len(items)
        cluster["representative_title"] = rep.get("title")
        cluster["rank_key"] = _candidate_rank_key_by_record(rep)
        clusters.append(cluster)

    clusters.sort(
        key=lambda c: c.get("rank_key", (float("-inf"), float("-inf"), float("-inf"), float("-inf"))),
        reverse=True,
    )
    total_clusters = len(clusters)
    return _paginate_clusters(clusters, limit=limit, offset=offset, total=total_clusters)

# Pagination helper
# ─────────────────────────────────────────────────────────────────────────────
def _paginate_clusters(clusters: List[Dict[str, Any]], *, limit: int, offset: int, total: int) -> Dict[str, Any]:
    limit_val = max(1, min(int(limit or 10), 200))
    offset_val = max(0, int(offset or 0))
    start = offset_val
    end = offset_val + limit_val
    paged_clusters = clusters[start:end]

    for c in paged_clusters:
        c.pop("rank_key", None)

    return {"clusters": paged_clusters, "total": total}
