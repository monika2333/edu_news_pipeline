from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.adapters.db import get_adapter
from src.domain.models import ExportCandidate

logger = logging.getLogger(__name__)

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
) -> Dict[str, Any]:
    _ensure_manual_filter_schema()
    adapter = get_adapter()
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    where_ready = "AND status = 'ready_for_export'" if only_ready else ""
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
        ORDER BY score DESC NULLS LAST, publish_time_iso DESC NULLS LAST, article_id ASC
        LIMIT %s OFFSET %s
    """
    count_query = f"""
        SELECT COUNT(*) AS total
        FROM news_summaries
        WHERE manual_status = %s
          {where_ready}
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(count_query, (manual_status,))
        total_row = cur.fetchone()
        total = int(total_row["total"]) if total_row else 0
        cur.execute(query, (manual_status, limit, offset))
        rows = cur.fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["summary"] = record.get("manual_summary") or record.get("llm_summary") or ""
        record["bonus_keywords"] = _bonus_keywords(record.get("score_details"))
        items.append(record)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def list_candidates(*, limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    logger.info("Listing candidates: limit=%s offset=%s", limit, offset)
    return _paginate_by_status("pending", limit=limit, offset=offset, only_ready=True)


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


def bulk_decide(
    *,
    selected_ids: Sequence[str],
    backup_ids: Sequence[str],
    discarded_ids: Sequence[str],
    actor: Optional[str] = None,
) -> Dict[str, int]:
    _ensure_manual_filter_schema()
    selected = _normalize_ids(selected_ids)
    backups = _normalize_ids(backup_ids)
    discarded = _normalize_ids(discarded_ids)
    logger.info(
        "Applying decisions: selected=%s backup=%s discarded=%s actor=%s",
        len(selected),
        len(backups),
        len(discarded),
        actor,
    )
    updated_selected = _apply_decision(status="selected", ids=selected, actor=actor)
    updated_backup = _apply_decision(status="backup", ids=backups, actor=actor)
    updated_discarded = _apply_decision(status="discarded", ids=discarded, actor=actor)
    logger.info(
        "Decision result: selected=%s backup=%s discarded=%s",
        updated_selected,
        updated_backup,
        updated_discarded,
    )
    return {"selected": updated_selected, "backup": updated_backup, "discarded": updated_discarded}


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
) -> Dict[str, Any]:
    _ensure_manual_filter_schema()
    adapter = get_adapter()
    fetch_query = """
        SELECT
            article_id,
            title,
            llm_summary,
            manual_summary,
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
        ORDER BY score DESC NULLS LAST, publish_time_iso DESC NULLS LAST, article_id ASC
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
        }
    logger.info("Preparing export payload: %s candidates found", len(candidates))

    def _normalized_sentiment(candidate: ExportCandidate) -> str:
        label = (candidate.sentiment_label or "").strip().lower()
        return "negative" if label == "negative" else "positive"

    def _ext_value(candidate: ExportCandidate) -> float:
        value = candidate.external_importance_score
        if value is None:
            return float("-inf")
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("-inf")

    def _score_value(candidate: ExportCandidate) -> float:
        value = candidate.score
        if value is None:
            return float("-inf")
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("-inf")

    def _rank_key(candidate: ExportCandidate) -> Tuple[float, float]:
        return (_ext_value(candidate), _score_value(candidate))

    def _format_number(value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.2f}".rstrip("0").rstrip(".")

    def _format_entry(candidate: ExportCandidate) -> str:
        title_line = (candidate.title or "").strip()
        summary_line = (candidate.summary or "").strip()
        display_source = (candidate.llm_source or candidate.source or "").strip()
        suffix = f" ({display_source})" if display_source else ""

        metrics_parts: List[str] = []
        ext_score_value = candidate.external_importance_score
        if ext_score_value is not None:
            ext_score_text = _format_number(ext_score_value) or str(ext_score_value)
            metrics_parts.append(f"external_importance={ext_score_text}")
        metrics_suffix = f" ({'; '.join(metrics_parts)})" if metrics_parts else ""
        body = summary_line + suffix + metrics_suffix if summary_line else ""
        return "\n".join(filter(None, [title_line, body]))

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
    bucket_definitions: List[Tuple[str, Tuple[str, str], str]] = [
        ("\u4eac\u5185\u6b63\u9762", ("internal", "positive"), "jingnei_positive"),
        ("\u4eac\u5185\u8d1f\u9762", ("internal", "negative"), "jingnei_negative"),
        ("\u4eac\u5916\u6b63\u9762", ("external", "positive"), "jingwai_positive"),
        ("\u4eac\u5916\u8d1f\u9762", ("external", "negative"), "jingwai_negative"),
    ]
    text_entries: List[str] = []
    export_payload: List[Tuple[ExportCandidate, str]] = []
    category_counts: Dict[str, int] = {}
    for label, key, section_key in bucket_definitions:
        bucket_items = sorted(bucket_index[key], key=_rank_key, reverse=True)
        category_counts[label] = len(bucket_items)
        if not bucket_items:
            continue
        export_payload.extend((item, section_key) for item in bucket_items)
        cluster_texts = [_format_entry(item) for item in bucket_items]
        header_line = f"[{label}] total {len(bucket_items)} items"
        block_text = header_line + "\n\n" + "\n\n---\n\n".join(cluster_texts)
        text_entries.append(block_text)

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
    export_text = "\n\n".join(text_entries)
    final_output.write_text(export_text, encoding="utf-8")

    adapter.record_export(report_tag, export_payload, output_path=str(final_output))
    if mark_exported:
        ids = [cid.filtered_article_id for cid, _ in export_payload]
        updated = _apply_decision(status="exported", ids=ids, actor=None)
        logger.info("Marked %s articles as exported", updated)
    return {
        "items": items,
        "count": len(items),
        "report_tag": report_tag,
        "output_path": str(final_output),
        "category_counts": category_counts,
        "content": export_text,
    }



__all__ = [
    "list_candidates",
    "list_review",
    "list_discarded",
    "bulk_decide",
    "save_edits",
    "export_batch",
    "status_counts",
    "reset_to_pending",
]
