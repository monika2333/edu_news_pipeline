"""
manual_filter_export.py

Export logic for generating final report text and recording export metadata.
Uses core/reporting for formatting and bucketing, keeps DB IO here.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.adapters.db import get_adapter
from src.core.reporting import build_buckets, format_export_text, resolve_periods
from src.domain.models import ExportCandidate

from .manual_filter_helpers import (
    DEFAULT_REPORT_TYPE,
    EXPORT_META_PATH,
    _attach_source_fields,
    _normalize_report_type,
)
from .manual_filter_decisions import _apply_decision

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Meta file I/O
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# File path utilities
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_unique(path: Path) -> Path:
    """Return a unique path by adding numeric suffix if needed."""
    if not path.exists():
        return path
    parent, stem, suffix = path.parent, path.stem, path.suffix
    counter = 1
    while True:
        candidate = parent / f"{stem}({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


# ─────────────────────────────────────────────────────────────────────────────
# Export batch
# ─────────────────────────────────────────────────────────────────────────────
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

    # ── DB read ──────────────────────────────────────────────────────────────
    adapter = get_adapter()
    rows = adapter.fetch_manual_selected_for_export(report_type=target_report_type)  # type: ignore[attr-defined]
    
    # Build ExportCandidate objects
    items: List[Dict[str, Any]] = []
    candidates: List[ExportCandidate] = []
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
        candidates.append(candidate)
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

    # ── Use core/reporting for formatting ────────────────────────────────────
    # Load meta and resolve periods
    meta_state = _load_export_meta()
    period_value, total_value, meta_state, today_iso = resolve_periods(
        template,
        period,
        total_period,
        report_type=target_report_type,
        meta_state=meta_state,
    )
    today_date = datetime.fromisoformat(today_iso).date()
    
    # Build buckets using core/reporting
    buckets, category_counts = build_buckets(candidates, template=template)
    
    # Format export text using core/reporting
    export_text = format_export_text(
        template=template,
        buckets=buckets,
        period=period_value,
        total=total_value,
        report_date=today_date,
    )
    
    # Build export payload for recording
    export_payload: List[Tuple[ExportCandidate, str]] = []
    for bucket_def, bucket_items in buckets:
        section_key = bucket_def["section"]
        export_payload.extend((item, section_key) for item in bucket_items)

    # ── File output ──────────────────────────────────────────────────────────
    base_output = Path(output_path)
    if not base_output.is_absolute():
        base_output = (Path.cwd() / base_output).resolve()
    base_output.parent.mkdir(parents=True, exist_ok=True)
    
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
        
        # Update meta state
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
