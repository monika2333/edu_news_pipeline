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

_SCHEMA_VERIFIED = False


def _ensure_manual_filter_schema() -> None:
    """
    Make sure the columns used by the manual filter console exist.

    This is lightweight and guarded to run only once per process. It prevents
    silent UPDATE failures when a new database is missing the manual_* columns.
    """
    global _SCHEMA_VERIFIED
    if _SCHEMA_VERIFIED:
        return
    adapter = get_adapter()
    statements = [
        "ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_status VARCHAR(50) DEFAULT 'pending'",
        "ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_summary TEXT",
        "ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_rank DOUBLE PRECISION",
        "ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_decided_by VARCHAR(100)",
        "ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_decided_at TIMESTAMPTZ",
        "UPDATE news_summaries SET manual_status = 'pending' WHERE manual_status IS NULL",
        "CREATE INDEX IF NOT EXISTS news_summaries_manual_status_idx ON news_summaries(manual_status)",
    ]
    try:
        with adapter._cursor() as cur:  # type: ignore[attr-defined]
            for stmt in statements:
                cur.execute(stmt)
        _SCHEMA_VERIFIED = True
        logger.debug("Manual filter schema verified/ensured")
    except Exception as exc:  # pragma: no cover - defensive guard for prod environments
        logger.warning("Manual filter schema check skipped: %s", exc)


def _period_increment_for_template(template: str) -> int:
    return 1 if template == "zongbao" else 2


DEFAULT_CLUSTER_THRESHOLD = 0.9


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
) -> Tuple[int, int, Dict[str, Any], str]:
    meta = _load_export_meta()
    today = date.today()
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


def _paginate_by_status(
    manual_status: str,
    *,
    limit: int,
    offset: int,
    only_ready: bool = False,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
) -> Dict[str, Any]:
    _ensure_manual_filter_schema()
    adapter = get_adapter()
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    where_ready = "AND status = 'ready_for_export'" if only_ready else ""

    conditions = []
    params: List[Any] = [manual_status]
    if region in ("internal", "external"):
        conditions.append("is_beijing_related = %s")
        params.append(True if region == "internal" else False)
    if sentiment in ("positive", "negative"):
        conditions.append("sentiment_label = %s")
        params.append(sentiment)
    extra_where = ""
    if conditions:
        extra_where = " AND " + " AND ".join(conditions)

    query = f"""
        SELECT
            article_id,
            title,
            llm_summary,
            manual_summary,
            score,
            source,
            publish_time,
            publish_time_iso,
            url,
            sentiment_label,
            sentiment_confidence,
            is_beijing_related,
            external_importance_score,
            manual_status,
            score_details
        FROM news_summaries
        WHERE manual_status = %s
          {where_ready}
          {extra_where}
        ORDER BY manual_rank ASC NULLS LAST,
                 score DESC NULLS LAST,
                 publish_time_iso DESC NULLS LAST,
                 article_id ASC
        LIMIT %s OFFSET %s
    """
    count_query = f"""
        SELECT COUNT(*) AS total
        FROM news_summaries
        WHERE manual_status = %s
          {where_ready}
          {extra_where}
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(count_query, tuple(params))
        total_row = cur.fetchone()
        total = int(total_row["total"]) if total_row else 0
        cur.execute(query, tuple(params + [limit, offset]))
        rows = cur.fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["summary"] = record.get("manual_summary") or record.get("llm_summary") or ""
        record["bonus_keywords"] = _bonus_keywords(record.get("score_details"))
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
) -> Dict[str, Any]:
    region = region if region in ("internal", "external") else None
    sentiment = sentiment if sentiment in ("positive", "negative") else None
    logger.info("Listing candidates: limit=%s offset=%s region=%s sentiment=%s", limit, offset, region, sentiment)
    if cluster:
        return cluster_pending(
            region=region,
            sentiment=sentiment,
            limit=limit,
            offset=offset,
            cluster_threshold=cluster_threshold,
        )
    return _paginate_by_status("pending", limit=limit, offset=offset, only_ready=True, region=region, sentiment=sentiment)


def _candidate_rank_key_by_record(record: Dict[str, Any]) -> Tuple[float, float, float]:
    ext_val = record.get("external_importance_score")
    ext_score = float(ext_val) if isinstance(ext_val, (int, float)) else float("-inf")
    score_val = record.get("score")
    score = float(score_val) if isinstance(score_val, (int, float)) else float("-inf")
    ts = record.get("publish_time_iso") or record.get("publish_time")
    ts_val = 0.0
    if ts:
        try:
            ts_val = datetime.fromisoformat(str(ts)).timestamp()
        except Exception:
            ts_val = 0.0
    return (ext_score, score, ts_val)


def _collect_pending(region: Optional[str], sentiment: Optional[str], fetch_limit: int = 5000) -> List[Dict[str, Any]]:
    _ensure_manual_filter_schema()
    adapter = get_adapter()
    conditions = ["manual_status = 'pending'", "status = 'ready_for_export'"]
    params: List[Any] = []
    if region in ("internal", "external"):
        conditions.append("is_beijing_related = %s")
        params.append(True if region == "internal" else False)
    if sentiment in ("positive", "negative"):
        conditions.append("sentiment_label = %s")
        params.append(sentiment)
    where_clause = " AND ".join(conditions)
    query = f"""
        SELECT
            article_id,
            title,
            llm_summary,
            manual_summary,
            manual_rank,
            score,
            content_markdown,
            url,
            source,
            publish_time_iso,
            publish_time,
            sentiment_label,
            sentiment_confidence,
            is_beijing_related,
            external_importance_score,
            external_importance_checked_at,
            score_details
        FROM news_summaries
        WHERE {where_clause}
        ORDER BY manual_rank ASC NULLS LAST,
                 score DESC NULLS LAST,
                 publish_time_iso DESC NULLS LAST,
                 article_id ASC
        LIMIT %s
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(query, tuple(params + [fetch_limit]))
        rows = cur.fetchall()
    records: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["summary"] = record.get("manual_summary") or record.get("llm_summary") or ""
        record["bonus_keywords"] = _bonus_keywords(record.get("score_details"))
        records.append(record)
    return records


def cluster_pending(
    *,
    region: Optional[str] = None,
    sentiment: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    cluster_threshold: Optional[float] = None,
) -> Dict[str, Any]:
    fetch_limit = 5000
    records = _collect_pending(region, sentiment, fetch_limit=fetch_limit)
    total = len(records)
    if not records:
        return {"clusters": [], "total": 0}

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
        threshold = cluster_threshold if cluster_threshold is not None else DEFAULT_CLUSTER_THRESHOLD
        try:
            threshold_val = float(threshold)
        except Exception:
            threshold_val = DEFAULT_CLUSTER_THRESHOLD
        threshold_val = max(0.0, min(threshold_val, 1.0))

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
                            "score": itm.get("score"),
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

    # 分页（按簇分页）
    limit = max(1, min(int(limit or 10), 200))
    offset = max(0, int(offset or 0))
    start = offset
    end = offset + limit
    paged_clusters = clusters[start:end]

    # 清理 rank_key 不返回
    for c in paged_clusters:
        c.pop("rank_key", None)

    return {"clusters": paged_clusters, "total": total_clusters}


def list_review(decision: str, *, limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    decision = decision if decision in ("selected", "backup") else "selected"
    logger.info("Listing review items: decision=%s limit=%s offset=%s", decision, limit, offset)
    return _paginate_by_status(decision, limit=limit, offset=offset, only_ready=False)


def list_discarded(*, limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    logger.info("Listing discarded items: limit=%s offset=%s", limit, offset)
    return _paginate_by_status("discarded", limit=limit, offset=offset, only_ready=False)


def status_counts() -> Dict[str, int]:
    _ensure_manual_filter_schema()
    adapter = get_adapter()
    query = """
        SELECT manual_status, COUNT(*) AS total
        FROM news_summaries
        GROUP BY manual_status
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(query)
        rows = cur.fetchall()
    counts: Dict[str, int] = {"pending": 0, "selected": 0, "backup": 0, "discarded": 0, "exported": 0}
    for row in rows:
        status = str(row.get("manual_status") or "").strip() or "pending"
        try:
            counts[status] = int(row.get("total") or 0)
        except Exception:
            counts[status] = 0
    return counts


def _apply_decision(
    *,
    status: str,
    ids: Sequence[str],
    actor: Optional[str] = None,
) -> int:
    adapter = get_adapter()
    payload = []
    now_ts = datetime.now(timezone.utc)
    for article_id in ids:
        payload.append((status, actor, now_ts, article_id))
    if not payload:
        return 0
    query = """
        UPDATE news_summaries
        SET manual_status = %s,
            manual_decided_by = COALESCE(%s, manual_decided_by),
            manual_decided_at = %s,
            updated_at = NOW()
        WHERE article_id = %s
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.executemany(query, payload)
        return cur.rowcount


def _next_rank(status: str) -> float:
    adapter = get_adapter()
    query = "SELECT COALESCE(MAX(manual_rank), 0) AS max_rank FROM news_summaries WHERE manual_status = %s"
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(query, (status,))
        row = cur.fetchone() or {}
    try:
        return float(row.get("max_rank") or 0.0)
    except Exception:
        return 0.0


def _apply_ranked_decision(
    *,
    status: str,
    ids: Sequence[str],
    actor: Optional[str],
    start_rank: float,
) -> int:
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    payload = []
    rank = start_rank
    for article_id in ids:
        payload.append((status, rank, actor, now_ts, article_id))
        rank += 1
    if not payload:
        return 0
    query = """
        UPDATE news_summaries
        SET manual_status = %s,
            manual_rank = %s,
            manual_decided_by = COALESCE(%s, manual_decided_by),
            manual_decided_at = %s,
            updated_at = NOW()
        WHERE article_id = %s
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.executemany(query, payload)
        return cur.rowcount


def bulk_decide(
    *,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    pending_ids: Sequence[str] = (),
    actor: Optional[str] = None,
) -> Dict[str, int]:
    _ensure_manual_filter_schema()
    selected = _normalize_ids(selected_ids)
    backups = _normalize_ids(backup_ids)
    discarded = _normalize_ids(discarded_ids)
    pending = _normalize_ids(pending_ids)
    logger.info(
        "Applying decisions: selected=%s backup=%s discarded=%s pending=%s actor=%s",
        len(selected),
        len(backups),
        len(discarded),
        len(pending),
        actor,
    )
    selected_rank_base = _next_rank("selected")
    backup_rank_base = _next_rank("backup")
    updated_selected = _apply_ranked_decision(
        status="selected",
        ids=selected,
        actor=actor,
        start_rank=selected_rank_base + 1,
    )
    updated_backup = _apply_ranked_decision(
        status="backup",
        ids=backups,
        actor=actor,
        start_rank=backup_rank_base + 1,
    )
    updated_discarded = _apply_decision(status="discarded", ids=discarded, actor=actor)
    updated_pending = reset_to_pending(pending, actor=actor)
    logger.info(
        "Decision result: selected=%s backup=%s discarded=%s pending=%s",
        updated_selected,
        updated_backup,
        updated_discarded,
        updated_pending,
    )
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
) -> Dict[str, int]:
    """
    Persist manual ordering for review lists and keep statuses in sync with list membership.
    """
    _ensure_manual_filter_schema()
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    payload: List[Tuple[Any, ...]] = []
    selected_ids = _normalize_ids(selected_order)
    backup_ids = _normalize_ids(backup_order)

    for index, aid in enumerate(selected_ids, start=1):
        payload.append(("selected", float(index), actor, now_ts, aid))
    for index, aid in enumerate(backup_ids, start=1):
        payload.append(("backup", float(index), actor, now_ts, aid))

    if not payload:
        return {"selected": 0, "backup": 0}

    query = """
        UPDATE news_summaries
        SET manual_status = %s,
            manual_rank = %s,
            manual_decided_by = COALESCE(%s, manual_decided_by),
            manual_decided_at = %s,
            updated_at = NOW()
        WHERE article_id = %s
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.executemany(query, payload)
        logger.info(
            "Updated manual ranks: selected=%s backup=%s rows=%s",
            len(selected_ids),
            len(backup_ids),
            cur.rowcount,
        )
        return {"selected": len(selected_ids), "backup": len(backup_ids), "updated_rows": cur.rowcount}


def reset_to_pending(ids: Sequence[str], *, actor: Optional[str] = None) -> int:
    _ensure_manual_filter_schema()
    target_ids = _normalize_ids(ids)
    if not target_ids:
        return 0
    logger.info("Resetting to pending: count=%s actor=%s", len(target_ids), actor)
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    payload = []
    for aid in target_ids:
        payload.append((actor, now_ts, aid))
    query = """
        UPDATE news_summaries
        SET manual_status = 'pending',
            manual_decided_by = COALESCE(%s, manual_decided_by),
            manual_decided_at = %s,
            updated_at = NOW()
        WHERE article_id = %s
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.executemany(query, payload)
        return cur.rowcount


def save_edits(edits: Dict[str, Dict[str, Any]], *, actor: Optional[str] = None) -> int:
    _ensure_manual_filter_schema()
    adapter = get_adapter()
    now_ts = datetime.now(timezone.utc)
    payload = []
    for aid, edit in edits.items():
        summary = edit.get("summary")
        if summary is None:
            continue
        payload.append((summary, actor, now_ts, aid))
    if not payload:
        return 0
    logger.info("Saving manual edits: count=%s actor=%s", len(payload), actor)
    query = """
        UPDATE news_summaries
        SET manual_summary = %s,
            manual_decided_by = COALESCE(%s, manual_decided_by),
            manual_decided_at = %s,
            updated_at = NOW()
        WHERE article_id = %s
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.executemany(query, payload)
        return cur.rowcount


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
) -> Dict[str, Any]:
    # 预览模式永不落盘或标记
    if dry_run:
        mark_exported = False

    _ensure_manual_filter_schema()
    adapter = get_adapter()
    fetch_query = """
        SELECT
            article_id,
            title,
            llm_summary,
            manual_summary,
            manual_rank,
            score,
            content_markdown,
            url,
            source,
            publish_time_iso,
            publish_time,
            sentiment_label,
            sentiment_confidence,
            is_beijing_related,
            external_importance_score,
            external_importance_checked_at
        FROM news_summaries
        WHERE manual_status = 'selected'
        ORDER BY manual_rank ASC NULLS LAST,
                 score DESC NULLS LAST,
                 publish_time_iso DESC NULLS LAST,
                 article_id ASC
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(fetch_query)
        rows = cur.fetchall()
    items: List[Dict[str, Any]] = []
    candidates: List[Tuple[ExportCandidate, str]] = []
    for row in rows:
        record = dict(row)
        summary_text = record.get("manual_summary") or record.get("llm_summary") or ""
        article_id = str(record.get("article_id") or "")
        title = record.get("title")
        article_hash = adapter._article_hash(article_id, record.get("url"), title)  # type: ignore[attr-defined]
        candidate = ExportCandidate(
            filtered_article_id=article_id,
            raw_article_id=article_id,
            article_hash=article_hash,
            title=title,
            summary=str(summary_text),
            content=str(record.get("content_markdown") or ""),
            source=record.get("source"),
            llm_source=None,
            score=float(record.get("score") or 0.0),
            original_url=record.get("url"),
            published_at=record.get("publish_time_iso") or record.get("publish_time"),
            sentiment_label=record.get("sentiment_label"),
            sentiment_confidence=record.get("sentiment_confidence"),
            is_beijing_related=record.get("is_beijing_related"),
            external_importance_score=record.get("external_importance_score"),
            external_importance_checked_at=record.get("external_importance_checked_at"),
        )
        candidates.append((candidate, section))
        items.append(
            {
                "article_id": article_id,
                "title": title,
                "summary": summary_text,
                "score": candidate.score,
                "source": record.get("source"),
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

    def _rank_key(candidate: ExportCandidate) -> Tuple[float, float]:
        ext_val = candidate.external_importance_score
        ext_score = float(ext_val) if isinstance(ext_val, (int, float)) else float("-inf")
        return (ext_score, _score_value(candidate))

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

    period_value, total_value, meta_state, today_iso = _resolve_periods(template, period, total_period)
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
        adapter.record_export(
            report_tag,
            export_payload,
            output_path=str(final_output),
        )
        if mark_exported:
            ids = [cid.filtered_article_id for cid, _ in export_payload]
            updated = _apply_decision(status="exported", ids=ids, actor=None)
            logger.info("Marked %s articles as exported", updated)
        meta_state[template] = {"period": period_value, "total": total_value, "date": today_iso}
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
