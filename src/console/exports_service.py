
from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from src.adapters.db import get_adapter


def _get_adapter_safe():
    try:
        return get_adapter()
    except Exception as exc:  # pragma: no cover - degrade gracefully
        print(f"[console] warning: database adapter unavailable: {exc}", file=sys.stderr)
        return None


def _normalize_artifacts(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _serialize_item(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row.get("id")),
        "article_id": row.get("article_id"),
        "section": row.get("section"),
        "order_index": int(row.get("order_index") or 0),
        "final_summary": row.get("final_summary"),
        "metadata": row.get("metadata") or {},
    }


def get_latest_export(include_items: bool = False) -> Optional[Dict[str, Any]]:
    adapter = _get_adapter_safe()
    if adapter is None:
        return None
    try:
        batch = adapter.fetch_latest_brief_batch()
    except Exception as exc:  # pragma: no cover - degrade gracefully
        print(f"[console] warning: failed to fetch latest brief batch: {exc}", file=sys.stderr)
        return None
    if not batch:
        return None
    batch_id = str(batch.get("id"))
    artifacts = _normalize_artifacts(batch.get("export_payload"))
    serialized_items = []
    item_count = 0
    if include_items:
        try:
            items = adapter.fetch_brief_items_by_batch(batch_id)
            serialized_items = [_serialize_item(item) for item in items]
            item_count = len(serialized_items)
        except Exception as exc:  # pragma: no cover
            print(f"[console] warning: failed to fetch export items: {exc}", file=sys.stderr)
            serialized_items = []
            item_count = 0
    else:
        try:
            item_count = adapter.fetch_brief_item_count(batch_id)
        except Exception as exc:  # pragma: no cover
            print(f"[console] warning: failed to count export items: {exc}", file=sys.stderr)
            item_count = 0

    result: Dict[str, Any] = {
        "batch_id": batch_id,
        "report_date": batch.get("report_date"),
        "sequence_no": int(batch.get("sequence_no") or 0),
        "generated_by": batch.get("generated_by"),
        "export_payload": artifacts,
        "created_at": batch.get("created_at"),
        "updated_at": batch.get("updated_at"),
        "items": serialized_items,
        "item_count": item_count,
    }
    return result


__all__ = ["get_latest_export"]
