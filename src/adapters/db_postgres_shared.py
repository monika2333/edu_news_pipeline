from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

MISSING = object()


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
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


__all__ = ["MISSING", "article_hash", "to_iso", "iso_datetime", "json_safe"]
