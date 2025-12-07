from __future__ import annotations

import logging
import json
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.adapters.db import get_adapter
from src.domain.models import ExportCandidate
from src.adapters.title_cluster import cluster_titles

logger = logging.getLogger(__name__)
EXPORT_META_PATH = Path("outputs/manual_filter_export_meta.json")
DEFAULT_REPORT_TYPE = "zongbao"
VALID_REPORT_TYPES = {"zongbao", "wanbao"}
_cluster_cache: Dict[Tuple[str, str, str, float], Dict[str, Any]] = {}


def _normalize_report_type(report_type: Optional[str]) -> str:
    value = (report_type or DEFAULT_REPORT_TYPE).strip().lower()
    return value if value in VALID_REPORT_TYPES else DEFAULT_REPORT_TYPE


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

def _period_increment_for_template(template: str) -> int:
    return 1 if template == "zongbao" else 2


DEFAULT_CLUSTER_THRESHOLD = 0.9


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


def _load_export_meta() -> Dict[str, Any]:
    if not EXPORT_META_PATH.exists():
        return {}
    try:
        return json.loads(EXPORT_META_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_export_meta(data: Dict[str, Any]) -> None:
    EXPORT_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_META_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_periods(
    template: str,
    provided_period: Optional[int],
    provided_total: Optional[int],
    *,
    report_type: str,
) -> Tuple[int, int, Dict[str, Any], str]:
    meta = _load_export_meta()
    today = date.today()
    normalized_report_type = _normalize_report_type(report_type)
    report_bucket = meta.get(normalized_report_type)
    if not isinstance(report_bucket, dict):
        report_bucket = {}
    tpl_meta = report_bucket.get(template) or {}
    if not tpl_meta and normalized_report_type == template:
        tpl_meta = meta.get(template) or {}
    last_date_raw = tpl_meta.get("date")
    last_period = int(tpl_meta.get("period") or 0)
    last_total = int(tpl_meta.get("total") or 0)
    inc = _period_increment_for_template(template)

    days = 1
    if last_date_raw:
        try:
            last_date = datetime.fromisoformat(last_date_raw).date()
            delta_days = (today - last_date).days
            days = max(1, delta_days or 1)
        except Exception:
            days = 1

    if provided_period is not None:
        period = int(provided_period)
    else:
        period = (last_period + inc * days) if last_period else inc

    if provided_total is not None:
        total = int(provided_total)
    else:
        total = (last_total + inc * days) if last_total else inc

    return period, total, meta, today.isoformat()


def _normalize_ids(ids: Iterable[str]) -> List[str]:
    seen = {}
    for raw in ids or []:
        if not raw:
            continue
        key = str(raw).strip()
        if not key:
            continue
        seen[key] = True
    return list(seen.keys())


def _bonus_keywords(score_details: Any) -> List[str]:
    if not isinstance(score_details, dict):
        return []
    matched = score_details.get("matched_rules")
    if not isinstance(matched, list):
        return []
    labels: List[str] = []
    for rule in matched:
        if not isinstance(rule, dict):
            continue
        label = rule.get("label") or rule.get("rule_id")
        if label:
            labels.append(str(label))
    return labels


def _resolved_llm_source(record: Dict[str, Any]) -> str:
    """
    Prefer manual override, then LLM-detected, then raw source.
    """
    manual = (record.get("manual_llm_source") or "").strip()
    llm = (record.get("llm_source") or "").strip()
    source = (record.get("source") or "").strip()
    return manual or llm or source


def _attach_source_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    record["llm_source_manual"] = (record.get("manual_llm_source") or "").strip()
    record["llm_source_raw"] = (record.get("llm_source") or "").strip()
    record["llm_source_display"] = _resolved_llm_source(record)
    return record


def _attach_group_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    region = "internal" if record.get("is_beijing_related") else "external"
    sentiment = "negative" if (record.get("sentiment_label") or "").lower() == "negative" else "positive"
    record["region"] = region
    record["sentiment_key"] = sentiment
    record["group_key"] = f"{region}_{sentiment}"
    return record


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


def _paginate_clusters(clusters: List[Dict[str, Any]], *, limit: int, offset: int, total: int) -> Dict[str, Any]:
    limit_val = max(1, min(int(limit or 10), 200))
    offset_val = max(0, int(offset or 0))
    start = offset_val
    end = offset_val + limit_val
    paged_clusters = clusters[start:end]

    for c in paged_clusters:
        c.pop("rank_key", None)

    return {"clusters": paged_clusters, "total": total}


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


def _apply_decision(
    *,
    status: str,
    ids: Sequence[str],
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> int:
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    target_report_type = _normalize_report_type(report_type)
    payload: List[Dict[str, Any]] = []
    for article_id in ids:
        if not article_id:
            continue
        payload.append(
            {
                "article_id": article_id,
                "status": status,
                "rank": None,
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )
    if not payload:
        return 0
    return adapter.update_manual_review_statuses(payload, report_type=target_report_type)  # type: ignore[attr-defined]


def _next_rank(status: str, *, report_type: str) -> float:
    adapter = get_adapter()
    target_report_type = _normalize_report_type(report_type)
    return adapter.manual_review_max_rank(status, report_type=target_report_type)  # type: ignore[attr-defined]


def _apply_ranked_decision(
    *,
    status: str,
    ids: Sequence[str],
    actor: Optional[str],
    start_rank: float,
    report_type: str,
) -> int:
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    target_report_type = _normalize_report_type(report_type)
    payload: List[Dict[str, Any]] = []
    rank = start_rank
    for article_id in ids:
        if not article_id:
            continue
        payload.append(
            {
                "article_id": article_id,
                "status": status,
                "rank": rank,
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )
        rank += 1
    if not payload:
        return 0
    return adapter.update_manual_review_statuses(payload, report_type=target_report_type)  # type: ignore[attr-defined]


def bulk_decide(
    *,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    pending_ids: Sequence[str] = (),
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, int]:
    selected = _normalize_ids(selected_ids)
    backups = _normalize_ids(backup_ids)
    discarded = _normalize_ids(discarded_ids)
    pending = _normalize_ids(pending_ids)
    target_report_type = _normalize_report_type(report_type)
    logger.info(
        "Applying decisions: selected=%s backup=%s discarded=%s pending=%s actor=%s report_type=%s",
        len(selected),
        len(backups),
        len(discarded),
        len(pending),
        actor,
        target_report_type,
    )
    selected_rank_base = _next_rank("selected", report_type=target_report_type)
    backup_rank_base = _next_rank("backup", report_type=target_report_type)
    updated_selected = _apply_ranked_decision(
        status="selected",
        ids=selected,
        actor=actor,
        start_rank=selected_rank_base + 1,
        report_type=target_report_type,
    )
    updated_backup = _apply_ranked_decision(
        status="backup",
        ids=backups,
        actor=actor,
        start_rank=backup_rank_base + 1,
        report_type=target_report_type,
    )
    updated_discarded = _apply_decision(status="discarded", ids=discarded, actor=actor, report_type=target_report_type)
    updated_pending = reset_to_pending(pending, actor=actor, report_type=target_report_type)
    logger.info(
        "Decision result: selected=%s backup=%s discarded=%s pending=%s",
        updated_selected,
        updated_backup,
        updated_discarded,
        updated_pending,
    )
    _prune_cluster_cache(selected + backups + discarded + pending)
    return {
        "selected": updated_selected,
        "backup": updated_backup,
        "discarded": updated_discarded,
        "pending": updated_pending,
    }


def update_ranks(
    *,
    selected_order: Sequence[str],
    backup_order: Sequence[str],
    actor: Optional[str] = None,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, int]:
    """
    Persist manual ordering for review lists and keep statuses in sync with list membership.
    """
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    target_report_type = _normalize_report_type(report_type)
    payload: List[Dict[str, Any]] = []
    selected_ids = _normalize_ids(selected_order)
    backup_ids = _normalize_ids(backup_order)

    for index, aid in enumerate(selected_ids, start=1):
        payload.append(
            {
                "article_id": aid,
                "status": "selected",
                "rank": float(index),
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )
    for index, aid in enumerate(backup_ids, start=1):
        payload.append(
            {
                "article_id": aid,
                "status": "backup",
                "rank": float(index),
                "report_type": target_report_type,
                "decided_by": actor,
                "decided_at": now_ts,
            }
        )

    if not payload:
        return {"selected": 0, "backup": 0}

    updated_rows = adapter.update_manual_review_statuses(payload, report_type=target_report_type)  # type: ignore[attr-defined]
    logger.info(
        "Updated manual ranks: selected=%s backup=%s rows=%s report_type=%s",
        len(selected_ids),
        len(backup_ids),
        updated_rows,
        target_report_type,
    )
    return {"selected": len(selected_ids), "backup": len(backup_ids), "updated_rows": updated_rows}


def reset_to_pending(ids: Sequence[str], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    target_ids = _normalize_ids(ids)
    if not target_ids:
        return 0
    target_report_type = _normalize_report_type(report_type)
    logger.info("Resetting to pending: count=%s actor=%s report_type=%s", len(target_ids), actor, target_report_type)
    adapter = get_adapter()
    return adapter.reset_manual_reviews_to_pending(target_ids, actor=actor, report_type=target_report_type)  # type: ignore[attr-defined]


def save_edits(edits: Dict[str, Dict[str, Any]], *, actor: Optional[str] = None, report_type: str = DEFAULT_REPORT_TYPE) -> int:
    adapter = get_adapter()
    if not edits:
        return 0
    target_report_type = _normalize_report_type(report_type)
    normalized: Dict[str, Dict[str, Any]] = {}
    for aid, payload in (edits or {}).items():
        summary = payload.get("summary")
        llm_source = payload.get("llm_source")
        notes = payload.get("notes")
        score = payload.get("score")
        normalized[aid] = {
            "summary": summary,
            "manual_llm_source": (llm_source or "").strip() if llm_source is not None else None,
            "notes": notes,
            "score": score,
            "report_type": target_report_type,
        }
    logger.info("Saving manual edits: count=%s actor=%s report_type=%s", len(edits), actor, target_report_type)
    return adapter.update_manual_review_summaries(normalized, actor=actor, report_type=target_report_type)  # type: ignore[attr-defined]


def export_batch(
    *,
    report_tag: str,
    section: str = "manual_filter",
    output_path: str = "outputs/manual_filter_export.txt",
    mark_exported: bool = True,
    template: str = "zongbao",
    period: Optional[int] = None,
    total_period: Optional[int] = None,
    dry_run: bool = False,
    report_type: str = DEFAULT_REPORT_TYPE,
) -> Dict[str, Any]:
    # 预览模式永不落盘或标记
    if dry_run:
        mark_exported = False
    target_report_type = _normalize_report_type(report_type)

    adapter = get_adapter()
    rows = adapter.fetch_manual_selected_for_export(report_type=target_report_type)  # type: ignore[attr-defined]
    items: List[Dict[str, Any]] = []
    candidates: List[Tuple[ExportCandidate, str]] = []
    for row in rows:
        record = _attach_source_fields(dict(row))
        summary_text = record.get("manual_summary") or record.get("llm_summary") or ""
        article_id = str(record.get("article_id") or "")
        title = record.get("title")
        article_hash = adapter._article_hash(article_id, record.get("url"), title)  # type: ignore[attr-defined]
        source_text = record.get("llm_source_display") or ""
        candidate = ExportCandidate(
            filtered_article_id=article_id,
            raw_article_id=article_id,
            article_hash=article_hash,
            title=title,
            summary=str(summary_text),
            content=str(record.get("content_markdown") or ""),
            source=record.get("source"),
            llm_source=source_text,
            score=float(record.get("score") or 0.0),
            original_url=record.get("url"),
            published_at=record.get("publish_time_iso") or record.get("publish_time"),
            sentiment_label=record.get("sentiment_label"),
            sentiment_confidence=record.get("sentiment_confidence"),
            is_beijing_related=record.get("is_beijing_related"),
            external_importance_score=record.get("external_importance_score"),
            external_importance_checked_at=record.get("external_importance_checked_at"),
            manual_rank=float(record["manual_rank"]) if record.get("manual_rank") is not None else None,
        )
        candidates.append((candidate, section))
        items.append(
            {
                "article_id": article_id,
                "report_type": record.get("report_type") or target_report_type,
                "title": title,
                "summary": summary_text,
                "score": candidate.score,
                "source": record.get("source"),
                "llm_source_display": source_text,
                "publish_time_iso": record.get("publish_time_iso"),
                "sentiment_label": record.get("sentiment_label"),
                "is_beijing_related": record.get("is_beijing_related"),
            }
        )
    if not candidates:
        logger.info("Export requested but no candidates found for report_tag=%s", report_tag)
        return {
            "items": [],
            "count": 0,
            "report_tag": report_tag,
            "output_path": output_path,
            "content": "",
            "category_counts": {},
            "period": period,
            "total_period": total_period,
            "template": template,
            "dry_run": dry_run,
            "report_type": target_report_type,
        }
    logger.info("Preparing export payload: %s candidates found", len(candidates))

    def _normalized_sentiment(candidate: ExportCandidate) -> str:
        label = (candidate.sentiment_label or "").strip().lower()
        return "negative" if label == "negative" else "positive"

    def _score_value(candidate: ExportCandidate) -> float:
        value = candidate.score
        if value is None:
            return float("-inf")
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("-inf")

    def _rank_key(candidate: ExportCandidate) -> Tuple[float, float, float, float]:
        ext_val = candidate.external_importance_score
        ext_score = float(ext_val) if isinstance(ext_val, (int, float)) else float("-inf")
        score = _score_value(candidate)
        if candidate.manual_rank is not None:
            return (1.0, -float(candidate.manual_rank), 0.0, 0.0)
        return (0.0, 0.0, ext_score, score)

    def _chinese_date(dt: date) -> str:
        return f"{dt.year}年{dt.month}月{dt.day}日"

    def _chinese_number(idx: int) -> str:
        numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五"]
        if 1 <= idx <= len(numerals):
            return numerals[idx - 1]
        return str(idx)

    def _bucket_definitions(tpl: str) -> List[Dict[str, Any]]:
        if tpl == "zongbao":
            return [
                {"key": ("internal", "negative"), "label": "【重点关注舆情】", "section": "jingnei_negative", "marker": "★", "numbered": False},
                {"key": ("internal", "positive"), "label": "【新闻信息纵览】", "section": "jingnei_positive", "marker": "■", "numbered": False},
                {"key": ("external", "negative"), "label": "【国内教育热点】", "section": "jingwai_negative", "marker": "▲", "numbered": False},
            ]
        return [
            {"key": ("internal", "positive"), "label": "【舆情速览】", "section": "jingnei_positive", "marker": None, "numbered": True},
            {"key": ("external", "positive"), "label": "【舆情参考】", "section": "jingwai_positive", "marker": None, "numbered": True},
        ]

    bucket_index: Dict[Tuple[str, str], List[ExportCandidate]] = {
        ("internal", "positive"): [],
        ("internal", "negative"): [],
        ("external", "positive"): [],
        ("external", "negative"): [],
    }
    for cand, _ in candidates:
        sentiment_bucket = _normalized_sentiment(cand)
        key = ("internal", sentiment_bucket) if cand.is_beijing_related else ("external", sentiment_bucket)
        bucket_index[key].append(cand)

    period_value, total_value, meta_state, today_iso = _resolve_periods(
        template,
        period,
        total_period,
        report_type=target_report_type,
    )
    today_date = datetime.fromisoformat(f"{today_iso}").date()

    bucket_defs = _bucket_definitions(template)
    export_payload: List[Tuple[ExportCandidate, str]] = []
    category_counts: Dict[str, int] = {}
    section_texts: List[str] = []

    for defn in bucket_defs:
        key = defn["key"]
        bucket_items = sorted(bucket_index[key], key=_rank_key, reverse=True)
        category_counts[defn["label"]] = len(bucket_items)
        if not bucket_items:
            continue
        export_payload.extend((item, defn["section"]) for item in bucket_items)

        lines: List[str] = [defn["label"]]
        for idx, cand in enumerate(bucket_items, start=1):
            title_text = (cand.title or "").strip()
            summary_text = (cand.summary or "").strip()
            source_text = (cand.llm_source or cand.source or "").strip()
            source_suffix = f"（{source_text}）" if source_text else ""
            summary_line = f"{summary_text}{source_suffix}".strip()

            if defn.get("numbered"):
                prefix = f"{_chinese_number(idx)}、"
            else:
                marker = defn.get("marker") or ""
                prefix = f"{marker} " if marker else ""
            lines.append(f"{prefix}{title_text}")
            if summary_line:
                lines.append(summary_line)
            lines.append("")  # blank line between items
        section_texts.append("\n".join(lines).rstrip())

    header_lines: List[str] = []
    if template == "zongbao":
        header_lines = [
            "首都教育每日舆情综报",
            f"{today_date.year}年第{period_value}期（总第{total_value}期）",
            _chinese_date(today_date),
        ]
    else:
        header_lines = [
            "首都教育舆情",
            f"总第{total_value}期",
            _chinese_date(today_date),
        ]

    export_text_body = "\n\n".join(section_texts).strip()
    export_text = "\n\n".join([line for line in ["\n".join(header_lines).strip(), export_text_body] if line]).strip()

    base_output = Path(output_path)
    if not base_output.is_absolute():
        base_output = (Path.cwd() / base_output).resolve()
    base_output.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_unique(path: Path) -> Path:
        if not path.exists():
            return path
        parent, stem, suffix = path.parent, path.stem, path.suffix
        counter = 1
        while True:
            candidate = parent / f"{stem}({counter}){suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    final_output = _ensure_unique(base_output)
    if not dry_run:
        final_output.write_text(export_text, encoding="utf-8")
        adapter.record_manual_export(
            report_tag,
            export_payload,
            output_path=str(final_output),
        )
        if mark_exported:
            ids = [cid.filtered_article_id for cid, _ in export_payload]
            updated = _apply_decision(status="exported", ids=ids, actor=None, report_type=target_report_type)
            logger.info("Marked %s articles as exported", updated)
        meta_state.setdefault(target_report_type, {})
        meta_state[target_report_type][template] = {"period": period_value, "total": total_value, "date": today_iso}
        try:
            _save_export_meta(meta_state)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Failed to persist export meta: %s", exc)
    else:
        final_output = Path("")
    return {
        "items": items,
        "count": len(items),
        "report_tag": report_tag,
        "output_path": str(final_output) if not dry_run else "",
        "category_counts": category_counts,
        "content": export_text,
        "period": period_value,
        "total_period": total_value,
        "template": template,
        "dry_run": dry_run,
        "report_type": target_report_type,
    }



__all__ = [
    "list_candidates",
    "list_review",
    "list_discarded",
    "bulk_decide",
    "update_ranks",
    "save_edits",
    "export_batch",
    "status_counts",
    "reset_to_pending",
]
