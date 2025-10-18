from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from src.adapters import db_postgres
from src.adapters.db_postgres import PostgresAdapter, get_adapter

SOURCE_PRIORITY_ORDER = [
    "新华网",
    "人民网",
    "光明日报",
    "北京日报",
    "新京报",
    "中国教育报",
]
SOURCE_PRIORITY_MAP: Dict[str, int] = {name: index for index, name in enumerate(SOURCE_PRIORITY_ORDER, start=1)}
MAX_NEIGHBOR_CHECKS = 50


def _normalize_source(source: Optional[str]) -> str:
    return (source or "").strip()


def _source_priority(source: Optional[str]) -> int:
    text = _normalize_source(source)
    if not text:
        return len(SOURCE_PRIORITY_MAP) + 1
    for key, rank in SOURCE_PRIORITY_MAP.items():
        if key in text:
            return rank
    return len(SOURCE_PRIORITY_MAP) + 1


def _coalesce_datetime(*values: Optional[datetime]) -> datetime:
    for value in values:
        if isinstance(value, datetime):
            return value
    return datetime.max.replace(tzinfo=timezone.utc)


def _publish_datetime(article: Dict[str, object]) -> datetime:
    publish_iso = article.get("publish_time_iso")
    publish_ts = article.get("publish_time")
    fetched_at = article.get("fetched_at")
    dt_iso: Optional[datetime] = publish_iso if isinstance(publish_iso, datetime) else None
    if dt_iso:
        return dt_iso
    if isinstance(publish_ts, (int, float)):
        return datetime.fromtimestamp(publish_ts, tz=timezone.utc)
    if isinstance(fetched_at, datetime):
        return fetched_at
    return datetime.max.replace(tzinfo=timezone.utc)


def _hamming_distance(hex_a: str, hex_b: str) -> int:
    return (int(hex_a, 16) ^ int(hex_b, 16)).bit_count()


class _UnionFind:
    def __init__(self) -> None:
        self._parent: Dict[str, str] = {}

    def find(self, item: str) -> str:
        parent = self._parent.get(item)
        if parent is None:
            self._parent[item] = item
            return item
        if parent != item:
            self._parent[item] = self.find(parent)
        return self._parent[item]

    def union(self, a: str, b: str) -> None:
        root_a = self.find(a)
        root_b = self.find(b)
        if root_a == root_b:
            return
        if root_a < root_b:
            self._parent[root_b] = root_a
        else:
            self._parent[root_a] = root_b


def _choose_master(cluster_ids: Sequence[str], articles: Dict[str, Dict[str, object]]) -> str:
    def sort_key(article_id: str) -> Tuple[int, datetime, datetime, str]:
        article = articles[article_id]
        priority = _source_priority(article.get("source"))
        publish_at = _publish_datetime(article)
        fetched_at = article.get("fetched_at") if isinstance(article.get("fetched_at"), datetime) else datetime.max.replace(tzinfo=timezone.utc)
        return (
            priority,
            publish_at,
            fetched_at,
            article_id,
        )

    return min(cluster_ids, key=sort_key)


def _build_clusters(articles: Dict[str, Dict[str, object]]) -> Dict[str, List[str]]:
    union_find = _UnionFind()

    # Union by exact hash
    hash_groups: Dict[str, List[str]] = defaultdict(list)
    for article_id, article in articles.items():
        content_hash = article.get("content_hash")
        if isinstance(content_hash, str) and content_hash:
            hash_groups[content_hash].append(article_id)
    for ids in hash_groups.values():
        if len(ids) <= 1:
            continue
        base = ids[0]
        for other in ids[1:]:
            union_find.union(base, other)

    # Union by fingerprint similarity (SimHash distance <= 3)
    prefix_groups: Dict[str, List[str]] = defaultdict(list)
    for article_id, article in articles.items():
        fingerprint = article.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint:
            prefix_groups[fingerprint[:4]].append(article_id)
    for group_ids in prefix_groups.values():
        if len(group_ids) <= 1:
            continue
        group_ids.sort(key=lambda aid: str(articles[aid].get("fingerprint") or ""))
        for idx, base_id in enumerate(group_ids):
            base_fingerprint = articles[base_id].get("fingerprint")
            if not isinstance(base_fingerprint, str):
                continue
            upper_bound = min(idx + 1 + MAX_NEIGHBOR_CHECKS, len(group_ids))
            for peer_id in group_ids[idx + 1 : upper_bound]:
                peer_fingerprint = articles[peer_id].get("fingerprint")
                if not isinstance(peer_fingerprint, str):
                    continue
                if _hamming_distance(base_fingerprint, peer_fingerprint) <= 3:
                    union_find.union(base_id, peer_id)

    clusters: Dict[str, List[str]] = defaultdict(list)
    for article_id in articles:
        root = union_find.find(article_id)
        clusters[root].append(article_id)
    return clusters


def run(*, adapter: Optional[PostgresAdapter] = None) -> int:
    adapter = adapter or get_adapter()
    rows = adapter.fetch_filtered_articles_for_dedup()
    if not rows:
        return 0

    articles: Dict[str, Dict[str, object]] = {}
    feature_updates: List[Tuple[Optional[str], Optional[str], str]] = []
    for row in rows:
        article_id = str(row.get("article_id"))
        if not article_id:
            continue
        record = dict(row)
        content_hash = record.get("content_hash")
        fingerprint = record.get("fingerprint")
        if (not content_hash) or (not fingerprint):
            content = record.get("content_markdown")
            computed_hash, computed_fingerprint = db_postgres._compute_content_features(content)
            updated = False
            if computed_hash and computed_hash != content_hash:
                record["content_hash"] = computed_hash
                content_hash = computed_hash
                updated = True
            if computed_fingerprint and computed_fingerprint != fingerprint:
                record["fingerprint"] = computed_fingerprint
                fingerprint = computed_fingerprint
                updated = True
            if updated and content_hash:
                feature_updates.append((content_hash, fingerprint, article_id))
        record.pop("content_markdown", None)
        articles[article_id] = record

    if feature_updates:
        adapter.update_filtered_article_features(feature_updates)

    clusters = _build_clusters(articles)
    updates: List[Tuple[str, str]] = []
    for cluster_ids in clusters.values():
        if not cluster_ids:
            continue
        master_id = _choose_master(cluster_ids, articles)
        for article_id in cluster_ids:
            desired_primary = master_id
            current_primary = str(articles[article_id].get("primary_article_id") or "")
            if not current_primary or current_primary != desired_primary:
                updates.append((desired_primary, article_id))
                articles[article_id]["primary_article_id"] = desired_primary

    if not updates:
        return 0

    adapter.update_filtered_primary_ids(updates)
    return len(updates)


__all__ = ["run"]
