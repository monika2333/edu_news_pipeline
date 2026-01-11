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
# In-memory cluster cache
# ─────────────────────────────────────────────────────────────────────────────
_cluster_cache: Dict[Tuple[str, str, str, float], Dict[str, Any]] = {}


def _cluster_cache_key(region: Optional[str], sentiment: Optional[str], threshold: float, report_type: str) -> Tuple[str, str, str, float]:
    return (region or "all", sentiment or "all", report_type, threshold)


def _prune_cluster_cache(decided_ids: Sequence[str]) -> None:
    decided = {str(i).strip() for i in decided_ids if i}
    if not decided or not _cluster_cache:
        return
    for key, payload in list(_cluster_cache.items()):
        clusters = payload.get("clusters", [])
        if not clusters:
            continue
        pruned: List[Dict[str, Any]] = []
        for cluster in clusters:
            items = cluster.get("items") or []
            kept = [itm for itm in items if str(itm.get("article_id") or "").strip() not in decided]
            if not kept:
                continue
            cluster["items"] = kept
            cluster["size"] = len(kept)
            pruned.append(cluster)
        _cluster_cache[key] = {"clusters": pruned, "total": len(pruned)}


def _invalidate_cluster_cache() -> None:
    _cluster_cache.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Pending total helper
# ─────────────────────────────────────────────────────────────────────────────
def _pending_total(adapter: Any, *, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    """Lightweight pending count; falls back to status counts on adapter that lacks the method."""
    target_type = _normalize_report_type(report_type)
    try:
        return int(adapter.manual_review_pending_count(report_type=target_type))  # type: ignore[attr-defined]
    except Exception:
        try:
            counts = adapter.manual_review_status_counts(report_type=target_type)  # type: ignore[attr-defined]
            return int(counts.get("pending", 0)) if isinstance(counts, dict) else 0
        except Exception:
            return 0


# ─────────────────────────────────────────────────────────────────────────────
# Rank key for sorting
# ─────────────────────────────────────────────────────────────────────────────
def _candidate_rank_key_by_record(record: Dict[str, Any]) -> Tuple[float, float, float]:
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
    score = _num(record.get("score"))
    ts_val = _ts(record.get("publish_time_iso") or record.get("publish_time"))
    return (ext_score, score, ts_val)


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
    fetch_limit = 5000
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    try:
        threshold_val = float(cluster_threshold) if cluster_threshold is not None else DEFAULT_CLUSTER_THRESHOLD
    except Exception:
        threshold_val = DEFAULT_CLUSTER_THRESHOLD
    threshold_val = max(0.0, min(threshold_val, 1.0))

    cache_key = _cluster_cache_key(region, sentiment, threshold_val, target_report_type)
    current_pending_total = _pending_total(adapter, report_type=target_report_type)
    if not force_refresh and cache_key in _cluster_cache:
        cached = _cluster_cache[cache_key]
        clusters = cached.get("clusters", [])
        total_clusters = cached.get("total", len(clusters))
        cached_pending_total = cached.get("pending_total")
        if cached_pending_total is not None and cached_pending_total == current_pending_total:
            return _paginate_clusters(clusters, limit=limit, offset=offset, total=total_clusters)

    records = _collect_pending(region, sentiment, fetch_limit=fetch_limit, adapter=adapter, report_type=target_report_type)
    if not records:
        _cluster_cache[cache_key] = {
            "clusters": [],
            "total": 0,
            "item_total": 0,
            "pending_total": current_pending_total,
        }
        return {"clusters": [], "total": 0}
    item_total = len(records)

    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {
        ("internal", "positive"): [],
        ("internal", "negative"): [],
        ("external", "positive"): [],
        ("external", "negative"): [],
    }
    for rec in records:
        reg_key = "internal" if rec.get("is_beijing_related") else "external"
        sent_key = "negative" if (rec.get("sentiment_label") or "").lower() == "negative" else "positive"
        bucket_key = (reg_key, sent_key)
        buckets[bucket_key].append(rec)

    clusters: List[Dict[str, Any]] = []
    for bucket_key, items in buckets.items():
        if not items:
            continue
        items_sorted = sorted(items, key=_candidate_rank_key_by_record, reverse=True)
        titles = [itm.get("title") or "" for itm in items_sorted]
        groups = cluster_titles(titles, threshold=threshold_val)
        if not groups:
            groups = [list(range(len(items_sorted)))]

        cluster_structs: List[Tuple[Tuple[float, float, float], List[Dict[str, Any]]]] = []
        for idx, group in enumerate(groups):
            group_items = [items_sorted[i] for i in group if 0 <= i < len(items_sorted)]
            if not group_items:
                continue
            group_items.sort(key=_candidate_rank_key_by_record, reverse=True)
            key_val = _candidate_rank_key_by_record(group_items[0])
            cluster_structs.append((key_val, group_items))

        if not cluster_structs:
            cluster_structs.append((_candidate_rank_key_by_record(items_sorted[0]), items_sorted))

        cluster_structs.sort(key=lambda x: x[0], reverse=True)
        for idx, (_, group_items) in enumerate(cluster_structs):
            rep = group_items[0]
            cluster_id = f"{bucket_key[0]}-{bucket_key[1]}-{idx}"
            clusters.append(
                {
                    "cluster_id": cluster_id,
                    "region": bucket_key[0],
                    "sentiment": bucket_key[1],
                    "size": len(group_items),
                    "representative": rep.get("title"),
                    "items": [
                        {
                            "article_id": itm.get("article_id"),
                            "title": itm.get("title"),
                            "summary": itm.get("summary"),
                            "source": itm.get("source"),
                            "llm_source_display": itm.get("llm_source_display"),
                            "llm_source_raw": itm.get("llm_source_raw"),
                            "llm_source_manual": itm.get("llm_source_manual"),
                            "score": itm.get("score"),
                            "external_importance_score": itm.get("external_importance_score"),
                            "sentiment_label": itm.get("sentiment_label"),
                            "is_beijing_related": itm.get("is_beijing_related"),
                            "url": itm.get("url"),
                            "bonus_keywords": itm.get("bonus_keywords"),
                        }
                        for itm in group_items
                    ],
                    "rank_key": _candidate_rank_key_by_record(rep),
                }
            )

    # 排序簇（与 export 类似，按代表项 rank 值）
    clusters.sort(key=lambda c: c.get("rank_key", (float("-inf"), float("-inf"), float("-inf"))), reverse=True)
    total_clusters = len(clusters)

    _cluster_cache[cache_key] = {
        "clusters": clusters,
        "total": total_clusters,
        "item_total": item_total,
        "pending_total": current_pending_total,
    }

    return _paginate_clusters(clusters, limit=limit, offset=offset, total=total_clusters)


# ─────────────────────────────────────────────────────────────────────────────
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
