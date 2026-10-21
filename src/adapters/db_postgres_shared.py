from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional
from uuid import UUID

def article_hash(article_id: Optional[str], original_url: Optional[str], title: Optional[str]) -> str:
    import hashlib

    basis = "-".join(filter(None, (article_id, original_url, title)))
    if not basis:
        basis = datetime.now(timezone.utc).isoformat()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def to_iso(publish_time: Optional[int]) -> Optional[str]:
    if publish_time is None:
        return None
    try:
        return datetime.fromtimestamp(int(publish_time), tz=timezone.utc).isoformat()
    except Exception:
        return None


def iso_datetime(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def keyword_labels_from_score_details(score_details: Any) -> list[str]:
    """Extract matched bonus-keyword labels from ``news_summaries.score_details``.

    The column is JSON from the score step; historical rows may be a list, null or
    a dict without ``matched_rules``. Only labels matter to the prompt test.
    """

    if not isinstance(score_details, Mapping):
        return []
    matched_rules = score_details.get("matched_rules")
    if not isinstance(matched_rules, list):
        return []
    labels: list[str] = []
    for rule in matched_rules:
        if not isinstance(rule, Mapping):
            continue
        label = rule.get("label") or rule.get("rule_id")
        if label:
            labels.append(str(label))
    return labels


__all__ = [
    "article_hash",
    "json_safe",
    "iso_datetime",
    "keyword_labels_from_score_details",
    "to_iso",
]
