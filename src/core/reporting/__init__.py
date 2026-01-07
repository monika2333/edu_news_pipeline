"""
src/core/reporting

Shared export formatting, bucketing, and period calculation utilities.
Used by both manual_filter_export and export_brief workers.
"""
from .buckets import build_buckets, get_bucket_definitions, normalize_sentiment, candidate_rank_key
from .formatters import format_export_text, format_section_text, chinese_date, chinese_number
from .periods import resolve_periods, period_increment_for_template

__all__ = [
    # Buckets
    "build_buckets",
    "get_bucket_definitions",
    "normalize_sentiment",
    "candidate_rank_key",
    # Formatters
    "format_export_text",
    "format_section_text",
    "chinese_date",
    "chinese_number",
    # Periods
    "resolve_periods",
    "period_increment_for_template",
]
