# News Pipeline Refactor Plan

## Goal
- Isolate expensive processing to keyword-relevant articles while keeping raw storage intact.
- Introduce deterministic deduplication (hash plus SimHash) before scoring, summarizing, and exporting.
- Preserve export ordering by city scope and sentiment while expanding to positive and negative buckets.

## Target State Overview
1. `crawl_sources.py` ingests sources, stores every article in `raw_articles`, and immediately inserts keyword hits into `filtered_articles` as `pending`.
2. A new `hash_primary_worker` computes content hashes and SimHash for `filtered_articles`, assigns `primary_article_id`, and moves primary articles to a new staging table `primary_articles`.
3. `score.py` consumes `primary_articles`, calculates relevance scores, and promotes records scoring 60 or higher to `news_summaries` as `pending`.
4. `summarize.py` processes `news_summaries`, produces summaries, and performs sentiment classification via the LLM API, adding a `sentiment_label` (`positive` or `negative`) alongside existing region labels.
5. `export_brief.py` runs on demand, reads `news_summaries`, and outputs reports ordered by Beijing-positive, Beijing-negative, Non-Beijing-positive, Non-Beijing-negative, each group sorted by score descending.

## Worker Responsibilities
- **crawl_sources.py**
  - Continues fetching articles, upserts into `raw_articles`.
  - Evaluates keyword hits and inserts minimal payload into `filtered_articles` with status `pending`.
- **hash_primary_worker (new)**
  - Polls `filtered_articles` rows marked `pending`.
  - Computes `content_hash` (exact) and SimHash (near-duplicate) fingerprints.
  - Resolves `primary_article_id`; updates secondary rows and inserts primaries into `primary_articles`.
  - Marks processed rows as `hashed`.
- **score.py**
  - Reads `primary_articles` with status `pending`.
  - Calculates relevance score; persists scores in `primary_articles` and `news_summaries`.
  - Marks rows scoring below 60 as `filtered_out` and those scoring 60 or higher as `scored` for summarization.
- **summarize.py**
  - Consumes `news_summaries` entries with status `pending`.
  - Calls summarization LLM and new sentiment API.
  - Writes summary, `sentiment_label`, and `sentiment_confidence`; marks row `ready_for_export`.
- **export_brief.py**
  - Triggered by `export` command.
  - Reads `news_summaries` where status `ready_for_export`.
  - Groups by region (Beijing, Non-Beijing) and sentiment, orders by score descending within each group, and generates the briefing.

## Data Model Changes
- **raw_articles**: unchanged structure; remains the source of truth for all crawled content.
- **filtered_articles**
  - Columns: `article_id` (PK, FK to `raw_articles`), `keywords`, `status`, `content_hash`, `simhash`, `primary_article_id`, timestamps, source metadata.
  - Indexes: `(status)`, `(primary_article_id)`, `(content_hash)`, `(simhash)`.
- **primary_articles (new)**
  - Columns: `article_id` (PK, FK to `filtered_articles`), `primary_article_id`, `score`, `status`, text fields required for scoring and summarizing.
  - Purpose: isolates primary records ready for scoring and downstream tasks.
- **news_summaries**
  - Add `sentiment_label`, `sentiment_confidence`, `status`, and ensure `score` persists.
  - Establish FK to `primary_articles.article_id` for referential integrity.

## Execution Flow
1. Crawl sources -> store raw article -> enqueue keyword hit into `filtered_articles`.
2. Hash and primary resolution -> populate `content_hash`, `simhash`, `primary_article_id` -> insert primaries into `primary_articles`.
3. Score worker -> evaluate primaries -> promote 60+ entries into `news_summaries`.
4. Summarize worker -> create summary and sentiment -> mark ready for export.
5. Export command -> generate ordered briefing.

## Implementation Phases
- [x] Apply schema changes (create `primary_articles`, extend `filtered_articles` and `news_summaries`, add indexes and triggers).
- [x] Update `crawl_sources.py` to write `filtered_articles` rows on keyword hits.
- [x] Build `hash_primary_worker` with hash utilities and SimHash integration.
- [x] Modify `score.py` to consume `primary_articles` and enforce the 60-point threshold.
- [ ] Extend `summarize.py` for sentiment classification and new status transitions.
- [ ] Update `export_brief.py` to emit four ordered buckets and rely on `sentiment_label`.
- [ ] Adjust orchestration (scheduler, CLI commands) to run workers in the new order.

## Backfill and Migration
- Script historical keyword hits into `filtered_articles` with initial status `pending`.
- Recompute hash and SimHash to populate `primary_article_id` and seed `primary_articles`.
- Re-run scoring to populate `news_summaries`, enqueue summaries for any missing sentiment or summary fields.
- Validate referential links across tables before cutting over live traffic.

## Monitoring and Alerting
- Track queue depths per status (`pending`, `hashed`, `scored`, `ready_for_export`).
- Monitor hash collision rate and SimHash distance thresholds.
- Alert on sentiment API latency and failure rates.
- Measure export freshness by the time between `ready_for_export` status and the last export run.

## Testing Strategy
- Unit tests for hash/SimHash computation and primary article resolution logic.
- Integration tests that push articles through the worker sequence using fixtures.
- Regression test verifying export ordering across the four sentiment buckets.
- Load tests for `hash_primary_worker` to confirm dedup throughput meets SLA.
