"""
periods.py

Period and total period calculation based on template and meta state.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple


def period_increment_for_template(template: str) -> int:
    """Return period increment value based on template type."""
    return 1 if template == "zongbao" else 2


def resolve_periods(
    template: str,
    provided_period: Optional[int],
    provided_total: Optional[int],
    *,
    report_type: str,
    meta_state: Dict[str, Any],
    today: Optional[date] = None,
) -> Tuple[int, int, Dict[str, Any], str]:
    """
    Calculate period and total period numbers based on template, meta state, and date.
    
    Args:
        template: Report template type (e.g., "zongbao", "wanbao")
        provided_period: Explicit period number, if provided
        provided_total: Explicit total period number, if provided
        report_type: Report type for bucketing in meta state
        meta_state: Existing meta state from file
        today: Override date for calculation (defaults to today)
    
    Returns:
        Tuple of (period, total, updated_meta_state, today_iso_string)
    """
    if today is None:
        today = date.today()
    
    # Normalize report type
    normalized_report_type = report_type.strip().lower() if report_type else "zongbao"
    if normalized_report_type not in ("zongbao", "wanbao"):
        normalized_report_type = "zongbao"
    
    # Get template-specific meta from report bucket
    report_bucket = meta_state.get(normalized_report_type)
    if not isinstance(report_bucket, dict):
        report_bucket = {}
    tpl_meta = report_bucket.get(template) or {}
    
    # Fallback: check top-level if report_type matches template
    if not tpl_meta and normalized_report_type == template:
        tpl_meta = meta_state.get(template) or {}
    
    last_date_raw = tpl_meta.get("date")
    last_period = int(tpl_meta.get("period") or 0)
    last_total = int(tpl_meta.get("total") or 0)
    inc = period_increment_for_template(template)
    
    # Calculate days since last export
    days = 1
    if last_date_raw:
        try:
            last_date = datetime.fromisoformat(last_date_raw).date()
            delta_days = (today - last_date).days
            days = max(1, delta_days or 1)
        except Exception:
            days = 1
    
    # Determine period
    if provided_period is not None:
        period = int(provided_period)
    else:
        period = (last_period + inc * days) if last_period else inc
    
    # Determine total
    if provided_total is not None:
        total = int(provided_total)
    else:
        total = (last_total + inc * days) if last_total else inc
    
    return period, total, meta_state, today.isoformat()
