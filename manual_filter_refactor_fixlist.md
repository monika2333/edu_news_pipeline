# Manual Filter Refactor Fix List

Purpose: list remaining issues after the split and how to fix them.

## Required fixes

1) Fix broken import in `dashboard.py`
   - Current: `from src.console.services import manual_filter`
   - Update to: `from src.console import manual_filter_service as manual_filter`
   - Reason: `src/console/services` no longer exists.

2) Fix broken import in `src/console/runs_service.py`
   - Current: `from src.console.services import exports as exports_service`
   - Update to: `from src.console import exports_service`
   - Reason: keep dashboard export snapshot working.

3) Restore default export path in `src/console/manual_filter_routes.py`
   - Current default: `outputs/manual_filter_service_export.txt`
   - Recommended: `outputs/manual_filter_export.txt`
   - Reason: avoid unexpected behavior changes.

## Optional cleanups

4) Consolidate export decision helper
   - `src/console/manual_filter_export.py` defines `_apply_decision_for_export` (duplicate of decisions logic)
   - Options:
     - Move a shared helper into `manual_filter_helpers.py`, or
     - Import the existing `_apply_decision` from `manual_filter_decisions.py` (if circular refs can be avoided).
   - Reason: reduce drift risk between duplicated logic.

## Validation

- `python -m pytest tests/test_manual_filter_service.py`
