"""
formatters.py

Text block generation for export reports including headers, titles, summaries, and source suffixes.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Tuple

from src.domain.models import ExportCandidate


# ─────────────────────────────────────────────────────────────────────────────
# Chinese formatting helpers
# ─────────────────────────────────────────────────────────────────────────────
def chinese_date(dt: date) -> str:
    """Format date as Chinese date string (e.g., 2025年1月7日)."""
    return f"{dt.year}年{dt.month}月{dt.day}日"


def chinese_number(idx: int) -> str:
    """Convert integer to Chinese numeral for list numbering."""
    numerals = ["一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二", "十三", "十四", "十五"]
    if 1 <= idx <= len(numerals):
        return numerals[idx - 1]
    return str(idx)


# ─────────────────────────────────────────────────────────────────────────────
# Section text formatting
# ─────────────────────────────────────────────────────────────────────────────
def format_section_text(
    bucket_def: Dict[str, Any],
    items: List[ExportCandidate],
) -> str:
    """
    Format a single section/bucket as text.
    
    Args:
        bucket_def: Bucket definition with label, marker, numbered, etc.
        items: Sorted list of candidates for this bucket
    
    Returns:
        Formatted text block for this section
    """
    if not items:
        return ""
    
    label = bucket_def["label"]
    marker = bucket_def.get("marker")
    numbered = bucket_def.get("numbered", False)
    
    lines: List[str] = [label]
    
    for idx, cand in enumerate(items, start=1):
        title_text = (cand.title or "").strip()
        summary_text = (cand.summary or "").strip()
        source_text = (cand.llm_source or cand.source or "").strip()
        source_suffix = f"（{source_text}）" if source_text else ""
        summary_line = f"{summary_text}{source_suffix}".strip()
        
        # Determine prefix
        if numbered:
            prefix = f"{chinese_number(idx)}、"
        elif marker:
            prefix = f"{marker} "
        else:
            prefix = ""
        
        lines.append(f"{prefix}{title_text}")
        if summary_line:
            lines.append(summary_line)
        lines.append("")  # blank line between items
    
    return "\n".join(lines).rstrip()


# ─────────────────────────────────────────────────────────────────────────────
# Header generation
# ─────────────────────────────────────────────────────────────────────────────
def format_header(
    template: str,
    report_date: date,
    period: int,
    total: int,
) -> str:
    """
    Generate report header based on template.
    
    Args:
        template: Template type (zongbao, wanbao)
        report_date: Date for the report
        period: Current period number
        total: Total period number
    
    Returns:
        Formatted header text
    """
    if template == "zongbao":
        lines = [
            "首都教育每日舆情综报",
            f"{report_date.year}年第{period}期（总第{total}期）",
            chinese_date(report_date),
        ]
    elif template == "wanbao":
        lines = [
            "首都教育舆情",
            f"总第{total}期",
            chinese_date(report_date),
        ]
    else:
        # Default/worker format - no header
        return ""
    
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Full export text
# ─────────────────────────────────────────────────────────────────────────────
def format_export_text(
    *,
    template: str,
    buckets: List[Tuple[Dict[str, Any], List[ExportCandidate]]],
    period: int,
    total: int,
    report_date: date,
) -> str:
    """
    Generate complete export text including header and all sections.
    
    Args:
        template: Template type for formatting
        buckets: List of (bucket_definition, sorted_items) tuples
        period: Current period number
        total: Total period number
        report_date: Date for the report
    
    Returns:
        Complete formatted export text
    """
    # Generate header
    header = format_header(template, report_date, period, total)
    
    # Generate section texts
    section_texts: List[str] = []
    for bucket_def, items in buckets:
        if items:
            section_text = format_section_text(bucket_def, items)
            if section_text:
                section_texts.append(section_text)
    
    # Combine
    body = "\n\n".join(section_texts).strip()
    
    if header:
        return "\n\n".join([header, body]).strip() if body else header
    return body
