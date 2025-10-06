# Feed-First Toutiao Ingestion Plan

## Goals
- Persist every new Toutiao feed item as soon as it is discovered, even if article details fail to load.
- Enrich stored feed records with full content and metadata once detail fetches succeed.
- Maintain clear bookkeeping so downstream workers know whether an article has detail content available.

## Current Decisions
- Detail fetch retries: attempt each article detail fetch up to 3 times; skip further attempts after the third failure within a run and log the miss.
- Downstream consumption: hide feed-only rows until detail enrichment succeeds (e.g. filter on `detail_fetched_at IS NOT NULL`).
- Content storage: keep only Markdown representations; no raw HTML persistence required.


## High-Level Flow
1. **Feed collection**: Gather feed items per author, track newly seen `article_id`s relative to the database.
2. **Feed upsert**: Convert every new feed item into a database row using only feed metadata (title, summary, metrics, etc.) and write them immediately.
3. **Detail enrichment**: For the same batch, attempt detail fetches. Successful fetches update the existing rows with `content_markdown`, canonical URL, and other fields that may differ from feed data.
4. **Retry handling**: Keep failed detail fetches flagged so future runs can retry without re-ingesting the feed data.

## Data Model Adjustments
- Add `detail_fetched_at timestamptz` (nullable) to `toutiao_articles` so we can distinguish feed ingestion time (`fetched_at`) from successful detail fetches.
- Consider a boolean `has_detail` (generated or computed) or reuse `content_markdown is not null` checks; update downstream queries to respect the new field if added.
- Ensure `content_markdown` remains nullable and default-safe when only feed data is present.
- Downstream workers should filter on `detail_fetched_at IS NOT NULL` (or equivalent) so consumers only see enriched articles.

## Worker Changes (`src/workers/crawl_toutiao.py`)
- Split the current `run()` logic into three phases: feed retrieval, feed upsert, detail enrichment.
- Replace the single call to `fetch_article_records()` with:
  - A new helper (e.g. `build_feed_rows`) that transforms `FeedItem` objects into dictionaries ready for DB insertion with partial data.
  - A filtered list of article IDs that still require detail fetch (new or missing `detail_fetched_at`).
  - A detail enrichment loop that reuses existing `fetch_info()` but updates rows via a dedicated adapter method (see below).
- Keep logging granular: log separate counts for feed upserts, detail successes, and detail failures.

## HTTP Adapter Changes (`src/adapters/http_toutiao.py`)
- Extract existing record-building into two helpers:
  - `feed_item_to_row(feed_item)` for the minimal feed payload.
  - `build_detail_update(feed_item, detail_payload)` for enrichment data.
- Adjust `fetch_article_records()` (or replace it) so it no longer controls DB writes; it should return detail payloads keyed by article ID, allowing the worker to decide how to persist them.

## Database Adapter Changes (`src/adapters/db_postgres.py`)
- Add `upsert_toutiao_feed_rows(rows)` that only writes feed-level columns (`token`, `profile_url`, `article_id`, `title`, `source`, `publish_time`, `publish_time_iso`, `url`, `summary`, `comment_count`, `digg_count`) and stamps `fetched_at` with `now()` if omitted.
- Add `update_toutiao_article_details(records)` that updates `content_markdown`, canonical URL, `detail_fetched_at`, and any other fields provided by `fetch_info()` without overwriting feed data unnecessarily.
- Reuse existing `upsert_toutiao_articles` or deprecate it after the refactor to avoid double responsibility.

## Retry & Tracking Strategy
- Attempt detail fetch up to three times per article in a run; after the third failure, skip further retries for that cycle and log the miss (optionally storing `detail_attempted_at` or a retry counter for later analysis).
- Leverage `existing_ids` cache to avoid reprocessing feed entries already stored, but allow detail enrichment to run on articles lacking `detail_fetched_at`.

## Testing & Validation
- Unit-test new helper functions that convert feed items to rows and merge detail payloads.
- Add integration tests for the worker to confirm:
  - Feed-only data persists when detail fetch raises an error.
  - A subsequent detail fetch updates the existing row without duplicating.
- Verify database migrations apply cleanly and that downstream workers (summarize/export) filter out feed-only rows until detail enrichment completes.

## Rollout Steps
- [x] Create migration adding `detail_fetched_at` (and optional flags) to `toutiao_articles` (`database/migrations/20251006090000_add_detail_fetched_at_to_toutiao_articles.sql`).
- [x] Update baseline schema to include the new column and index (`database/schema.sql`).
- [x] Implement adapter and worker changes outlined above (`src/adapters/db_postgres.py`, `src/workers/crawl_toutiao.py`).
- [x] Update other pipeline stages to respect the new detail-ready signal (`src/adapters/db_postgres.py`, dependent workers).
- [x] Backfill: run the crawler once post-migration (`python -m src.cli.main crawl --limit 5000`); no new feed items were available, but pipeline executed successfully.
- [ ] Monitor logs for remaining detail failures and adjust retry thresholds as needed.

## Open Questions
- None at this time; see Current Decisions for stakeholder guidance.
