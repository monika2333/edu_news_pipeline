# Manual Filter Refactor Plan

Goal: split `manual_filter_service.py` into smaller, single-purpose modules with minimal behavior change and keep the public API stable.

## Steps

1) Define responsibilities
   - List/filter APIs
   - Cluster logic
   - Export logic
   - Metadata/cache helpers

2) Target files (flat, no new directories)
   - `src/console/manual_filter_helpers.py`
   - `src/console/manual_filter_cluster.py`
   - `src/console/manual_filter_export.py`
   - `src/console/manual_filter_decisions.py`
   - Keep `src/console/manual_filter_service.py` as the public facade

3) Move pure helpers + constants first
   - Examples: `_normalize_report_type`, `_bonus_keywords`, `DEFAULT_*`
   - This step avoids introducing circular imports later.

4) Move cluster/cache logic
   - `_cluster_cache`, `_cluster_cache_key`, `_prune_cluster_cache`, `cluster_pending`, `_collect_pending`

5) Move export logic
   - `export_batch`, `_resolve_periods`, `_load_export_meta`, `_save_export_meta`, and formatting helpers

6) Move decision/update logic
   - `bulk_decide`, `update_ranks`, `reset_to_pending`, `save_edits`

7) Rebuild the facade in `manual_filter_service.py`
   - Re-export functions via imports
   - Re-export `EXPORT_META_PATH` and other public constants
   - Maintain the existing `__all__`

8) Validate
   - `python -m pytest tests/test_manual_filter_service.py`
   - Optional: `python -m pytest -v`

## Function-to-module map (target)

- helpers: `_normalize_report_type`, `_normalize_ids`, `_bonus_keywords`, `_attach_source_fields`, `_attach_group_fields`, `DEFAULT_*`, `VALID_REPORT_TYPES`
- cluster: `_cluster_cache*`, `_collect_pending`, `_candidate_rank_key_by_record`, `cluster_pending`, `_paginate_clusters`
- export: `_load_export_meta`, `_save_export_meta`, `_resolve_periods`, `_period_increment_for_template`, `export_batch`
- decisions: `bulk_decide`, `update_ranks`, `reset_to_pending`, `save_edits`, `_apply_decision`, `_apply_ranked_decision`, `_next_rank`
- service facade: `list_candidates`, `list_review`, `list_discarded`, `status_counts` plus re-exports above

## Touch points to update

- `src/console/manual_filter_routes.py` should still import from `src/console/manual_filter_service.py`.
- `tests/test_manual_filter_service.py` should still import from `src/console/manual_filter_service.py`.
- `dashboard.py` currently imports `src.console.services.manual_filter`; update if still present.

## Acceptance criteria

- No changes to route paths or response payloads.
- All existing imports of `manual_filter_service` continue to work.
- `tests/test_manual_filter_service.py` passes.

## Notes

- No API behavior changes in this pass.
- Keep imports aligned with project standards.
- Avoid introducing circular dependencies; helpers should not import from other new modules.
