"""
manual_filter_helpers.py

Shared constants and helper functions for manual filter service.
These utilities are intentionally kept dependency-free to avoid circular imports.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_REPORT_TYPE = "zongbao"
VALID_REPORT_TYPES = {"zongbao", "wanbao"}


# ─────────────────────────────────────────────────────────────────────────────
# Report type normalization
# ─────────────────────────────────────────────────────────────────────────────
def _normalize_report_type(report_type: Optional[str]) -> str:
    value = (report_type or DEFAULT_REPORT_TYPE).strip().lower()
    return value if value in VALID_REPORT_TYPES else DEFAULT_REPORT_TYPE


# ─────────────────────────────────────────────────────────────────────────────
# ID normalization
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Score details / bonus keywords
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Source field helpers
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Group field helpers
# ─────────────────────────────────────────────────────────────────────────────
def _attach_group_fields(record: Dict[str, Any]) -> Dict[str, Any]:
    region = "internal" if record.get("is_beijing_related") else "external"
    sentiment = "negative" if (record.get("sentiment_label") or "").lower() == "negative" else "positive"
    record["region"] = region
    record["sentiment_key"] = sentiment
    record["group_key"] = f"{region}_{sentiment}"
    return record
