"""
manual_filter_export.py

Export logic for generating final report text and recording export metadata.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.adapters.db import get_adapter
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
# Period calculation
# ─────────────────────────────────────────────────────────────────────────────
def _period_increment_for_template(template: str) -> int:
    return 1 if template == "zongbao" else 2


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
