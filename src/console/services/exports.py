from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.adapters.db_supabase import get_adapter


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
    adapter = get_adapter()
    batch = adapter.fetch_latest_brief_batch()
    if not batch:
        return None
    batch_id = str(batch.get("id"))
    artifacts = _normalize_artifacts(batch.get("export_payload"))
    if include_items:
        items = adapter.fetch_brief_items_by_batch(batch_id)
        serialized_items = [_serialize_item(item) for item in items]
        item_count = len(serialized_items)
    else:
        serialized_items = []
        item_count = adapter.fetch_brief_item_count(batch_id)

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
