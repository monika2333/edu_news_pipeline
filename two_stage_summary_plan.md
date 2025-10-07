# Two-Stage Summary Pipeline Plan

## Objectives
- Persist keyword-positive articles into `news_summaries` immediately, even before LLM summarisation.
- Allow the summarisation worker to operate as a queue consumer, enriching only rows that still lack `llm_summary` (or are flagged as `pending`).
- Provide robust retry/monitoring hooks so failed LLM calls can be retried without losing feed candidates.

## Key Design Decisions
- Introduce a lightweight status model on `news_summaries` (`summary_status`, `summary_attempted_at`, `summary_fail_count`) to track progress and failures.
- Shift keyword filtering logic to a pre-processing flow capable of inserting `pending` rows (can live in crawl worker or a dedicated `prepare_summaries` worker).
- Update `summarize` worker to fetch pending rows from `news_summaries` instead of `toutiao_articles`, with optimistic locking to prevent double processing.

## Schema & Migration
1. Add columns to `news_summaries`:
   - `summary_status text NOT NULL DEFAULT 'pending'` (values: `pending`, `completed`, `failed`).
   - `summary_attempted_at timestamptz`.
   - `summary_fail_count integer NOT NULL DEFAULT 0`.
2. Backfill existing rows:
   - Set `summary_status='completed'` for rows where `llm_summary` is non-empty.
   - Leave others as `pending`.
3. Optional index on `(summary_status, summary_attempted_at)` to speed up queue queries.

## Adapter Changes (`src/adapters/db_postgres.py`)
- Add `insert_pending_summary(article)` helper for UPSERTing pre-filtered articles (only feed metadata, no summary).
- Add `fetch_pending_summaries(limit, *, max_attempts=None)` returning rows with `summary_status='pending'`, optionally filtering by `summary_fail_count`.
- Add `mark_summary_attempt(article_id)` to bump `summary_attempted_at` and increment `summary_fail_count` when a worker takes responsibility for a row.
- Add `complete_summary(article_id, summary_text, llm_source, keywords)` to update summary fields, status, timestamps.
- Add `mark_summary_failed(article_id, error_msg=None)` to set status `failed` after exceeding retry policy.

## Worker Changes
### Keyword Pre-Processing (Crawl or New Worker)
- After keyword match passes, call `insert_pending_summary()` instead of waiting for `summarize` to pick it up.
- Ensure UPSERT does not clobber existing `llm_summary`/`summary_status` when re-running.

### Summarize Worker (`src/workers/summarize.py`)
- Swap source query to `fetch_pending_summaries()`.
- For each batch row:
  1. Call `mark_summary_attempt()` before hitting the LLM.
  2. Run `summarise()` and `detect_source()`.
  3. On success, call `complete_summary()`; on failure, optionally retry locally, otherwise leave as `pending` (if retries remain) or `failed`.
- Maintain cursor logic for chronological ordering if still required, or replace with status ordering.

### Optional New Worker
- Provide a `retry_failed_summaries` CLI entry that requeues rows with `summary_status='failed'` but `summary_fail_count` below threshold.

## CLI & Operational Flow
1. Run `crawl` (or dedicated `prepare_summaries`) to insert pending records into `news_summaries`.
2. Run `summarize` which now strictly processes `pending` rows.
3. Run `score` and `export` as before; they should skip rows where `summary_status!='completed'` or `llm_summary` is empty.

## Testing Strategy
- Unit tests for new adapter methods (insert, fetch, transition helpers).
- Integration test ensuring keyword-positive articles become pending rows and are later completed by `summarize`.
- Regression tests verifying existing `score`/`export` flows ignore `pending` rows.

## Rollout Steps
1. Write migration adding status columns and backfilling existing data.
2. Implement adapter helpers and update `summarize` logic.
3. Adjust crawl/prep flow to insert `pending` summaries on keyword hit.
4. Update CLI help text/README to document the two-stage pipeline.
5. Deploy and monitor: verify queues drain, failure metrics, and that summaries keep flowing end-to-end.

## Confirmed Decisions
- Keyword filtering remains part of the crawl workflow; pending summary rows are inserted during the same run.
- LLM summarisation retries: attempt up to 3 times per article; mark the row as `failed` if all retries exhaust.
- CLI should report the count and IDs of `failed` summaries at the end of each run; no additional alerting is planned.

## Implementation Checklist
- [x] Apply migration adding status fields to `news_summaries`.
- [x] Update DB adapter with pending queue helpers and optimistic locking transitions.
- [x] Modify `crawl` (or prep flow) to insert pending summary rows on keyword hit.
- [x] Refactor `summarize` worker to consume pending rows and handle retries/status.
- [x] Adjust CLI/help docs to describe the two-stage summary process.
- [x] Validate end-to-end flow (manual run + tests).
