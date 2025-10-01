from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.adapters.db_supabase import get_adapter


def _normalize_plan(raw_plan: Any) -> List[str]:
    if isinstance(raw_plan, list):
        return [str(item) for item in raw_plan if item is not None]
    return []


def _normalize_artifacts(raw_artifacts: Any) -> Dict[str, Any]:
    if isinstance(raw_artifacts, dict):
        return raw_artifacts
    return {}


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _serialize_run(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": row.get("run_id", ""),
        "status": row.get("status", "unknown"),
        "trigger_source": row.get("trigger_source"),
        "plan": _normalize_plan(row.get("plan")),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "steps_completed": int(row.get("steps_completed") or 0),
        "artifacts": _normalize_artifacts(row.get("artifacts")),
        "error_summary": row.get("error_summary"),
    }


def _serialize_step(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "order_index": int(row.get("order_index") or 0),
        "step_name": row.get("step_name", ""),
        "status": row.get("status", "unknown"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "duration_seconds": _to_float(row.get("duration_seconds")),
        "error": row.get("error"),
    }


def list_pipeline_runs(limit: int = 20) -> List[Dict[str, Any]]:
    adapter = get_adapter()
    rows = adapter.fetch_pipeline_runs(limit=limit)
    return [_serialize_run(row) for row in rows]


def get_pipeline_run(run_id: str) -> Optional[Dict[str, Any]]:
    adapter = get_adapter()
    row = adapter.fetch_pipeline_run(run_id)
    if not row:
        return None
    steps = adapter.fetch_pipeline_run_steps(run_id)
    detail = _serialize_run(row)
    detail["steps"] = [_serialize_step(step) for step in steps]
    return detail


__all__ = ["get_pipeline_run", "list_pipeline_runs"]
