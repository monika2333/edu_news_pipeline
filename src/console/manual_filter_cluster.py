"""
manual_filter_cluster.py

Clustering logic and in-memory cache for grouping pending articles by title similarity.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from src.adapters.db_postgres_core import get_adapter
from src.adapters.title_cluster import (
    EMBEDDING_MODEL_NAME,
    cluster_embeddings,
    encode_texts,
    pack_embedding,
    unpack_embedding,
)

from .manual_filter_helpers import (
    DEFAULT_REPORT_TYPE,
    _normalize_report_type,
)
from .manual_filter_serializers import serialize_manual_filter_item

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


def _title_hash(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def _load_title_embedding_map(
    records: Sequence[Dict[str, Any]],
    *,
    adapter: Any,
) -> Dict[str, np.ndarray]:
    title_metadata = {
        str(record["article_id"]): {
            "title": str(record.get("title") or ""),
            "title_hash": _title_hash(str(record.get("title") or "")),
        }
        for record in records
        if record.get("article_id")
    }
    cached_rows = adapter.fetch_news_title_embeddings(
        list(title_metadata),
    )
    embeddings: Dict[str, np.ndarray] = {}
    for row in cached_rows:
        article_id = str(row.get("article_id") or "")
        metadata = title_metadata.get(article_id)
        if metadata is None:
            continue
        if str(row.get("model") or "") != EMBEDDING_MODEL_NAME:
            continue
        if str(row.get("title_hash") or "") != metadata["title_hash"]:
            continue
        try:
            vector = unpack_embedding(bytes(row["embedding"]))
        except (KeyError, TypeError, ValueError):
            continue
        if vector.size:
            embeddings[article_id] = vector

    missing_ids = [
        article_id
        for article_id in title_metadata
        if article_id not in embeddings
    ]
    logger.info(
        "Title embedding cache: %s hits, %s misses",
        len(embeddings),
        len(missing_ids),
    )
    if not missing_ids:
        return embeddings

    encoded = np.asarray(
        encode_texts([
            title_metadata[article_id]["title"]
            for article_id in missing_ids
        ]),
        dtype=np.float32,
    )
    if encoded.ndim != 2 or len(encoded) != len(missing_ids):
        raise RuntimeError("Title embedding encoder returned an invalid matrix")
    payload = []
    for article_id, vector in zip(missing_ids, encoded):
        embeddings[article_id] = vector
        payload.append(
            {
                "article_id": article_id,
                "embedding": pack_embedding(vector),
                "model": EMBEDDING_MODEL_NAME,
                "title_hash": title_metadata[article_id]["title_hash"],
            }
        )
    adapter.upsert_news_title_embeddings(payload)
    return embeddings


def refresh_clusters(
    *,
    cluster_threshold: Optional[float] = None,
) -> bool:
    adapter = get_adapter()
    try:
        threshold_val = float(cluster_threshold) if cluster_threshold is not None else DEFAULT_CLUSTER_THRESHOLD
    except Exception:
        threshold_val = DEFAULT_CLUSTER_THRESHOLD
    threshold_val = max(0.0, min(threshold_val, 1.0))

    if not adapter.try_advisory_lock(MANUAL_CLUSTER_LOCK_ID):
        return False

    try:
        records = _collect_pending(None, None, fetch_limit=5000, adapter=adapter)
        embedding_map = _load_title_embedding_map(
            records,
            adapter=adapter,
        )
        buckets: Dict[str, List[Dict[str, Any]]] = {key: [] for key in CLUSTER_BUCKET_KEYS}
        for record in records:
            bucket_key = _bucket_key_for_record(record)
            buckets.setdefault(bucket_key, []).append(record)

        clusters: List[Dict[str, Any]] = []
        for bucket_key, items in buckets.items():
            if not items:
                continue
            items_sorted = [
                item
                for item in sorted(
                    items,
                    key=_candidate_rank_key_by_record,
                    reverse=True,
                )
                if str(item.get("article_id") or "") in embedding_map
            ]
            if not items_sorted:
                continue
            matrix = np.stack([
                embedding_map[str(item["article_id"])]
                for item in items_sorted
            ])
            groups = cluster_embeddings(
                matrix,
                threshold=threshold_val,
            ) or [list(range(len(items_sorted)))]

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
                    }
                )

        adapter.replace_manual_clusters(clusters)  # type: ignore[attr-defined]
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
) -> List[Dict[str, Any]]:
    adapter = adapter or get_adapter()
    rows = adapter.fetch_manual_pending_for_cluster(  # type: ignore[attr-defined]
        region=region,
        sentiment=sentiment,
        fetch_limit=fetch_limit,
    )
    records: List[Dict[str, Any]] = []
    for row in rows:
        record = serialize_manual_filter_item(
            dict(row),
            fallback_status="pending",
            report_type=DEFAULT_REPORT_TYPE,
        )
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
    hide_submitted: bool = False,
) -> Dict[str, Any]:
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    try:
        threshold_val = float(cluster_threshold) if cluster_threshold is not None else DEFAULT_CLUSTER_THRESHOLD
    except Exception:
        threshold_val = DEFAULT_CLUSTER_THRESHOLD
    threshold_val = max(0.0, min(threshold_val, 1.0))

    if force_refresh:
        refresh_clusters(cluster_threshold=threshold_val)

    bucket_key = _bucket_key_from_filters(region, sentiment)
    rows = adapter.fetch_manual_clusters(  # type: ignore[attr-defined]
        bucket_key=bucket_key,
        hide_submitted=hide_submitted,
    )
    if not rows:
        return {"clusters": [], "total": 0, "item_total": 0}

    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        record = serialize_manual_filter_item(
            dict(row),
            fallback_status="pending",
            report_type=target_report_type,
        )
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
                "score_feedback": record.get("score_feedback"),
                "sentiment_label": record.get("sentiment_label"),
                "is_beijing_related": record.get("is_beijing_related"),
                "llm_source_display": record.get("llm_source_display"),
                "llm_source_raw": record.get("llm_source_raw"),
                "llm_source_manual": record.get("llm_source_manual"),
                "bonus_keywords": record.get("bonus_keywords"),
                "manual_rank": record.get("manual_rank"),
                "version": record.get("version"),
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
    total_items = sum(len(cluster.get("items") or []) for cluster in clusters)
    return _paginate_clusters(clusters, limit=limit, offset=offset, total=total_clusters, item_total=total_items)

# Pagination helper
# ─────────────────────────────────────────────────────────────────────────────
def _paginate_clusters(
    clusters: List[Dict[str, Any]],
    *,
    limit: int,
    offset: int,
    total: int,
    item_total: int,
) -> Dict[str, Any]:
    limit_val = max(1, min(int(limit or 10), 200))
    offset_val = max(0, int(offset or 0))
    start = offset_val
    end = offset_val + limit_val
    paged_clusters = clusters[start:end]

    for c in paged_clusters:
        c.pop("rank_key", None)

    return {"clusters": paged_clusters, "total": total, "item_total": item_total}
