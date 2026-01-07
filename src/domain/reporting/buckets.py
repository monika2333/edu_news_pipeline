"""
buckets.py

Sentiment/region bucketing, sorting, and ordering for export candidates.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from src.domain.models import ExportCandidate


# ─────────────────────────────────────────────────────────────────────────────
# Bucket definitions by template
# ─────────────────────────────────────────────────────────────────────────────
def get_bucket_definitions(template: str) -> List[Dict[str, Any]]:
    """
    Return bucket definitions for a given template.
    
    Each definition includes:
        - key: (region, sentiment) tuple
        - label: Display label for the section
        - section: Section identifier for export records
        - marker: Optional marker character (e.g., ★, ■, ▲)
        - numbered: Whether to use numbered list format
    """
    if template == "zongbao":
        return [
            {"key": ("internal", "negative"), "label": "【重点关注舆情】", "section": "jingnei_negative", "marker": "★", "numbered": False},
            {"key": ("internal", "positive"), "label": "【新闻信息纵览】", "section": "jingnei_positive", "marker": "■", "numbered": False},
            {"key": ("external", "negative"), "label": "【国内教育热点】", "section": "jingwai_negative", "marker": "▲", "numbered": False},
        ]
    elif template == "wanbao":
        return [
            {"key": ("internal", "positive"), "label": "【舆情速览】", "section": "jingnei_positive", "marker": None, "numbered": True},
            {"key": ("external", "positive"), "label": "【舆情参考】", "section": "jingwai_positive", "marker": None, "numbered": True},
        ]
    else:
        # Default/worker format - all four buckets
        return [
            {"key": ("internal", "positive"), "label": "京内正面", "section": "jingnei_positive", "marker": None, "numbered": False},
            {"key": ("internal", "negative"), "label": "京内负面", "section": "jingnei_negative", "marker": None, "numbered": False},
            {"key": ("external", "positive"), "label": "京外正面", "section": "jingwai_positive", "marker": None, "numbered": False},
            {"key": ("external", "negative"), "label": "京外负面", "section": "jingwai_negative", "marker": None, "numbered": False},
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Sentiment normalization
# ─────────────────────────────────────────────────────────────────────────────
def normalize_sentiment(candidate: ExportCandidate) -> str:
    """Normalize sentiment label to 'positive' or 'negative'."""
    label = (candidate.sentiment_label or "").strip().lower()
    return "negative" if label == "negative" else "positive"


# ─────────────────────────────────────────────────────────────────────────────
# Ranking key functions
# ─────────────────────────────────────────────────────────────────────────────
def _external_importance_value(candidate: ExportCandidate) -> float:
    """Extract external importance score as float."""
    value = candidate.external_importance_score
    if value is None:
        return float("-inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _score_value(candidate: ExportCandidate) -> float:
    """Extract score as float."""
    value = candidate.score
    if value is None:
        return float("-inf")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def candidate_rank_key(candidate: ExportCandidate) -> Tuple[float, float, float, float]:
    """
    Generate a rank key for sorting candidates.
    
    Priority order:
    1. Manual rank (if set) - lower rank = higher priority
    2. External importance score (higher = better)
    3. Score (higher = better)
    
    Returns a tuple suitable for sorting in descending order.
    """
    ext_score = _external_importance_value(candidate)
    score = _score_value(candidate)
    
    if candidate.manual_rank is not None:
        # Manual rank takes precedence; negate for descending sort
        return (1.0, -float(candidate.manual_rank), 0.0, 0.0)
    return (0.0, 0.0, ext_score, score)


def candidate_rank_key_simple(candidate: ExportCandidate) -> Tuple[float, float]:
    """
    Simplified rank key for worker export (no manual rank).
    Returns (external_importance_score, score) for sorting.
    """
    return (_external_importance_value(candidate), _score_value(candidate))


# ─────────────────────────────────────────────────────────────────────────────
# Bucket building
# ─────────────────────────────────────────────────────────────────────────────
def build_buckets(
    candidates: List[ExportCandidate],
    *,
    template: str,
) -> Tuple[List[Tuple[Dict[str, Any], List[ExportCandidate]]], Dict[str, int]]:
    """
    Build ordered bucket list from candidates based on template.
    
    Args:
        candidates: List of export candidates
        template: Template type for bucket definitions
    
    Returns:
        Tuple of:
        - List of (bucket_definition, sorted_items) tuples
        - Category counts dict mapping label -> count
    """
    # Initialize bucket index
    bucket_index: Dict[Tuple[str, str], List[ExportCandidate]] = {
        ("internal", "positive"): [],
        ("internal", "negative"): [],
        ("external", "positive"): [],
        ("external", "negative"): [],
    }
    
    # Distribute candidates into buckets
    for cand in candidates:
        sentiment = normalize_sentiment(cand)
        region = "internal" if cand.is_beijing_related else "external"
        bucket_index[(region, sentiment)].append(cand)
    
    # Get bucket definitions for template
    bucket_defs = get_bucket_definitions(template)
    
    # Build result
    result_buckets: List[Tuple[Dict[str, Any], List[ExportCandidate]]] = []
    category_counts: Dict[str, int] = {}
    
    for defn in bucket_defs:
        key = defn["key"]
        items = bucket_index.get(key, [])
        # Sort by rank key (descending)
        sorted_items = sorted(items, key=candidate_rank_key, reverse=True)
        category_counts[defn["label"]] = len(sorted_items)
        result_buckets.append((defn, sorted_items))
    
    return result_buckets, category_counts
