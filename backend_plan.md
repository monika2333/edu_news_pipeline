# Backend Implementation & Debugging Plan

The frontend dashboard has been implemented, but the "Submit" action (and possibly other write actions) is not producing the expected results. This plan outlines the steps to verify, debug, and fix the backend services.

## 1. Database Schema Verification
The `manual_filter` service relies on specific columns in the `news_summaries` table.
- **Action**: Connect to the database and verify the existence of the following columns:
    -   `manual_status` (VARCHAR/TEXT)
    -   `manual_summary` (TEXT)
    -   `manual_decided_by` (VARCHAR/TEXT)
    -   `manual_decided_at` (TIMESTAMP)
- **Fix**: If missing, execute the following SQL:
    ```sql
    ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_status VARCHAR(50) DEFAULT 'pending';
    ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_summary TEXT;
    ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_decided_by VARCHAR(100);
    ALTER TABLE news_summaries ADD COLUMN IF NOT EXISTS manual_decided_at TIMESTAMP WITH TIME ZONE;
    CREATE INDEX IF NOT EXISTS idx_news_summaries_manual_status ON news_summaries(manual_status);
    ```

## 2. API Payload & Type Verification
The frontend sends `article_id` as strings. If the database uses `INTEGER` for `article_id`, the SQL driver might handle it, but it's a potential source of failure if strict typing is enforced.
- **Action**: Check the `article_id` column type in `news_summaries`.
- **Action**: Inspect the server logs when clicking "Submit". Look for:
    -   422 Validation Errors (Pydantic)
    -   500 Internal Server Errors (SQL failures)
- **Fix**: If Pydantic validation fails, ensure `BulkDecideRequest` and `SaveEditsRequest` models in `src/console/routes/manual_filter.py` match the incoming JSON exactly.
    -   Current `BulkDecideRequest`: `selected_ids: List[str]`, etc.
    -   Current `SaveEditsRequest`: `edits: Dict[str, Dict[str, Any]]`.
    -   Ensure frontend sends matching structure.

## 3. Debugging `manual_filter.py` Service
The `bulk_decide` and `save_edits` functions perform `UPDATE` operations.
- **Action**: Add logging to `src/console/services/manual_filter.py` to trace execution.
    ```python
    import logging
    logger = logging.getLogger(__name__)

    def bulk_decide(...):
        logger.info(f"Deciding: selected={len(selected_ids)}, backup={len(backup_ids)}, discarded={len(discarded_ids)}")
        # ...
        logger.info(f"Updated: selected={updated_selected}, ...")
    ```
- **Action**: Verify that `_apply_decision` actually commits the transaction. The `adapter._cursor()` context manager usually handles commit/rollback, but verify `src/adapters/db_postgres.py` implementation ensures `commit()` is called on exit.

## 4. Frontend-Backend Integration Check
- **Action**: Open Browser Developer Tools (F12) -> Network Tab.
- **Action**: Click "Submit".
- **Check**:
    -   Request URL: `/api/manual_filter/edit` and `/api/manual_filter/decide`
    -   Request Payload: Check JSON body.
    -   Response Status: 200 OK?
    -   Response Body: `{"updated": N}` or `{"selected": N, ...}`.
- **Issue**: If Response is 200 but counts are 0, then the `WHERE article_id = %s` clause is not matching any rows.
    -   **Cause**: ID mismatch (e.g., "123" vs 123, or whitespace).
    -   **Fix**: Ensure `_normalize_ids` handles the input correctly.

## 5. Export Functionality
- **Action**: Verify `export_batch` logic.
- **Check**: Does it correctly fetch 'selected' items?
- **Check**: Does it write to the correct `output_path`?
- **Check**: Does it return the file content to the frontend for the popup? (Current implementation in `routes/manual_filter.py` attempts to read the file).

## Execution Order
1.  Check Server Logs (immediate feedback).
2.  Verify DB Schema (common cause for new features).
3.  Add Logging (if logs are silent).
4.  Fix Code/Schema based on findings.
