from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.adapters.db import get_adapter
from src.domain.models import ExportCandidate


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


def list_candidates(*, limit: int = 30, offset: int = 0) -> Dict[str, Any]:
    adapter = get_adapter()
    limit = max(1, min(int(limit or 30), 200))
    offset = max(0, int(offset or 0))
    query = """
        SELECT
            article_id,
            title,
            llm_summary,
            manual_summary,
            score,
            manual_score,
            source,
            publish_time,
            publish_time_iso,
            url,
            sentiment_label,
            sentiment_confidence,
            is_beijing_related,
            external_importance_score,
            manual_status,
            manual_notes,
            manual_decided_by,
            manual_decided_at
        FROM news_summaries
        WHERE status = 'ready_for_export'
          AND manual_status = 'pending'
        ORDER BY score DESC NULLS LAST, publish_time_iso DESC NULLS LAST, article_id ASC
        LIMIT %s OFFSET %s
    """
    count_query = """
        SELECT COUNT(*) AS total
        FROM news_summaries
        WHERE status = 'ready_for_export'
          AND manual_status = 'pending'
    """
    with adapter._cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(count_query)
        total_row = cur.fetchone()
        total = int(total_row["total"]) if total_row else 0
        cur.execute(query, (limit, offset))
        rows = cur.fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        record["summary"] = record.get("manual_summary") or record.get("llm_summary") or ""
        items.append(record)
    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _apply_decision(
    *,
    status: str,
    ids: Sequence[str],
    edits: Optional[Dict[str, Dict[str, Any]]] = None,
    actor: Optional[str] = None,
) -> int:
    adapter = get_adapter()
    payload = []
    now_ts = datetime.now(timezone.utc)
    for article_id in ids:
        edit = (edits or {}).get(article_id) or {}
        payload.append(
            (
                status,
                edit.get("summary"),
                edit.get("score"),
                edit.get("notes"),
                actor,
                now_ts,
                article_id,
            )
        )
    if not payload:
        return 0
    query = """
        UPDATE news_summaries
        SET manual_status = %s,
            manual_summary = COALESCE(%s, manual_summary, llm_summary),
            manual_score = COALESCE(%s, manual_score),
            manual_notes = COALESCE(%s, manual_notes),
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
    approved_ids: Sequence[str],
    discarded_ids: Sequence[str],
    edits: Optional[Dict[str, Dict[str, Any]]] = None,
    actor: Optional[str] = None,
) -> Dict[str, int]:
    approved = _normalize_ids(approved_ids)
    discarded = _normalize_ids(discarded_ids)
    updated_approved = _apply_decision(status="approved", ids=approved, edits=edits, actor=actor)
    updated_discarded = _apply_decision(status="discarded", ids=discarded, edits=edits, actor=actor)
    return {"approved": updated_approved, "discarded": updated_discarded}


def export_batch(
    *,
    report_tag: str,
    section: str = "manual_filter",
    output_path: str = "outputs/manual_filter_export.txt",
    mark_exported: bool = True,
) -> Dict[str, Any]:
    adapter = get_adapter()
    fetch_query = """
        SELECT
            article_id,
            title,
            llm_summary,
            manual_summary,
            score,
            manual_score,
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
        WHERE manual_status = 'approved'
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
            score=float(record.get("manual_score") or record.get("score") or 0.0),
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
        return {"items": [], "count": 0, "report_tag": report_tag, "output_path": output_path}

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
        suffix = f"（{display_source}）" if display_source else ""

        metrics_parts: List[str] = []
        ext_score_value = candidate.external_importance_score
        if ext_score_value is not None:
            ext_score_text = _format_number(ext_score_value) or str(ext_score_value)
            metrics_parts.append(f"external_importance={ext_score_text}")
        metrics_suffix = f"（{'; '.join(metrics_parts)}）" if metrics_parts else ""
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
        ("京内正面", ("internal", "positive"), "jingnei_positive"),
        ("京内负面", ("internal", "negative"), "jingnei_negative"),
        ("京外正面", ("external", "positive"), "jingwai_positive"),
        ("京外负面", ("external", "negative"), "jingwai_negative"),
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
        header_line = f"【{label}】共 {len(bucket_items)} 条"
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
    final_output.write_text("\n\n".join(text_entries), encoding="utf-8")

    adapter.record_export(report_tag, export_payload, output_path=str(final_output))
    if mark_exported:
        ids = [cid.filtered_article_id for cid, _ in export_payload]
        _apply_decision(status="exported", ids=ids, edits=None, actor=None)
    return {
        "items": items,
        "count": len(items),
        "report_tag": report_tag,
        "output_path": str(final_output),
        "category_counts": category_counts,
    }


__all__ = ["list_candidates", "bulk_decide", "export_batch"]
