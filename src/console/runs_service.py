
from __future__ import annotations

import sys
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from scripts.run_pipeline_once import DEFAULT_PIPELINE, run_pipeline_once
from src.adapters.db import get_adapter

_ALLOWED_STEPS: Set[str] = set(DEFAULT_PIPELINE)
_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 100


def _get_adapter_safe() -> Optional[Any]:
    try:
        return get_adapter()
    except Exception as exc:  # pragma: no cover - log and degrade gracefully
        print(f"[console] warning: database adapter unavailable: {exc}", file=sys.stderr)
        return None
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


def _build_plan(steps: Optional[Sequence[str]], skip: Optional[Iterable[str]]) -> List[str]:
    plan = list(steps) if steps else list(DEFAULT_PIPELINE)
    invalid = [step for step in plan if step not in _ALLOWED_STEPS]
    if invalid:
        raise ValueError(f"Unknown step(s): {', '.join(invalid)}")
    if skip:
        skip_set = {step for step in skip if step in _ALLOWED_STEPS}
        plan = [step for step in plan if step not in skip_set]
    if not plan:
        raise ValueError("Plan cannot be empty after applying skips")
    return plan


def list_pipeline_runs(limit: int = _DEFAULT_LIST_LIMIT) -> List[Dict[str, Any]]:
    effective_limit = max(1, min(limit, _MAX_LIST_LIMIT))
    adapter = _get_adapter_safe()
    if adapter is None:
        return []
    try:
        rows = adapter.fetch_pipeline_runs(limit=effective_limit)
    except Exception as exc:  # pragma: no cover - log and degrade gracefully
        print(f"[console] warning: failed to fetch pipeline runs: {exc}", file=sys.stderr)
        return []
    return [_serialize_run(row) for row in rows]


def get_pipeline_run(run_id: str) -> Optional[Dict[str, Any]]:
    adapter = _get_adapter_safe()
    if adapter is None:
        return None
    try:
        row = adapter.fetch_pipeline_run(run_id)
    except Exception as exc:  # pragma: no cover
        print(f"[console] warning: failed to fetch pipeline run {run_id}: {exc}", file=sys.stderr)
        return None
    if not row:
        return None
    try:
        steps = adapter.fetch_pipeline_run_steps(run_id)
    except Exception as exc:  # pragma: no cover
        print(f"[console] warning: failed to fetch run steps {run_id}: {exc}", file=sys.stderr)
        steps = []
    detail = _serialize_run(row)
    detail["steps"] = [_serialize_step(step) for step in steps]
    return detail


def get_latest_pipeline_run(include_steps: bool = True) -> Optional[Dict[str, Any]]:
    runs = list_pipeline_runs(limit=1)
    if not runs:
        return None
    latest = runs[0]
    if not include_steps:
        return latest
    detail = get_pipeline_run(latest["run_id"])
    return detail or latest


def trigger_pipeline_run(
    *,
    steps: Optional[Sequence[str]] = None,
    skip: Optional[Sequence[str]] = None,
    continue_on_error: bool = False,
    trigger_source: str = "console-api",
    record_metadata: bool = True,
) -> Dict[str, Any]:
    plan = _build_plan(steps, skip)
    adapter = _get_adapter_safe()
    record_flag = record_metadata and adapter is not None
    result = run_pipeline_once(
        plan,
        continue_on_error=continue_on_error,
        trigger_source=trigger_source,
        record_metadata=record_flag,
        adapter=adapter,
    )
    payload = result.to_dict()
    return payload


def get_dashboard_snapshot(limit: int = 10) -> Dict[str, Any]:
    runs = list_pipeline_runs(limit)
    latest_run = get_latest_pipeline_run(include_steps=True)
    latest_export = None
    try:
        from src.console.services import exports as exports_service

        latest_export = exports_service.get_latest_export(include_items=False)
    except Exception as exc:  # pragma: no cover - dashboard should degrade gracefully
        print(f"[console] warning: failed to load latest export: {exc}", file=sys.stderr)
        latest_export = None
    return {
        "runs": runs,
        "latest_run": latest_run,
        "latest_export": latest_export,
    }


__all__ = [
    "get_dashboard_snapshot",
    "get_latest_pipeline_run",
    "get_pipeline_run",
    "list_pipeline_runs",
    "trigger_pipeline_run",
]
