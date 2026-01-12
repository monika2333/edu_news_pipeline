Archive Mark Exported Plan

Goals
- Make "Archive" mark all items in the current review list (selected or backup) as exported.
- Remove deprecated manual export file/recording code paths.
- Keep behavior scoped to the review tab and current report type.

Non-Goals
- Generating export text files or batch history records.
- Changing bucket/template logic for other pipelines.

Plan
1. Remove manual export backend: delete `manual_filter_export.py`, the `/export` route, and related exports in `manual_filter_service.py`.
2. Add a simple archive API that accepts a list of article IDs and marks them `exported` via `_apply_decision`.
3. Update review tab `handleArchive()` to send all IDs from the current view and use the new archive API.
4. Clean up unused constants/imports tied to manual export metadata.
5. Do a quick manual check: archive in selected/backup and verify counts update.
