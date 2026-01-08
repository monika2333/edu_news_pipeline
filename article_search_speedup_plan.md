# Article Search Speedup Plan (Keywords Search + content_markdown)

## Goal
- Improve `/articles/search` responsiveness while keeping `content_markdown` in the keyword search scope.
- Keep the UI focused on keyword search (remove unused filters).

## Current Bottleneck (Observed)
- `ILIKE` search across `title`, `content_markdown`, and `llm_summary` in `news_summaries` forces large sequential scans.
- `content_markdown` is large; it is the biggest contributor to slow scans.

## Plan

### Phase 1: UI + API Simplification
- UI: simplify search form to keywords input + page size only.
- API: accept only `q`, `page`, `limit` (other filters removed).

### Phase 2: Trigram Index on a Combined Search Field (Chosen)
- Create a combined text field for search (title + llm_summary + content_markdown).
- Add a trigram GIN index on the combined field.
- Update the query to use a single `ILIKE` against the combined field.

Suggested SQL (Postgres 12+ for generated column):
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE news_summaries
ADD COLUMN search_text text GENERATED ALWAYS AS (
    coalesce(title, '') || ' ' ||
    coalesce(llm_summary, '') || ' ' ||
    coalesce(content_markdown, '')
) STORED;

CREATE INDEX CONCURRENTLY IF NOT EXISTS news_summaries_search_text_trgm
ON news_summaries
USING gin (search_text gin_trgm_ops);
```

Query change (example):
```sql
WHERE search_text ILIKE %s
```

Fallback if generated columns are not available:
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS news_summaries_search_expr_trgm
ON news_summaries
USING gin (
    (coalesce(title, '') || ' ' || coalesce(llm_summary, '') || ' ' || coalesce(content_markdown, ''))
    gin_trgm_ops
);
```
Then use the same concatenation expression in the query predicate.

### Phase 3: Cleanup
- Remove deprecated filter fields from `/articles/search` UI and docs.
- Update the prompt doc to reflect keywords-only search.

## Code Touchpoints
- `src/adapters/db_postgres.py` (`search_news_summaries`): replace the three-column `ILIKE` clause with `search_text ILIKE`.
- `src/console/articles_service.py`: remove unused filter arguments (`sources`, `sentiments`, `statuses`, `start_date`, `end_date`).
- `src/console/articles_routes.py`: drop unused query params, keep `q`, `page`, `limit`.
- `src/console/web_routes.py`: simplify `/articles/search` form handling to only accept keywords and pagination.
- `src/console/web_templates/search.html`: remove filter inputs for source/sentiment/status/date.
- `README.md`: update the `/articles/search` description to "keywords only".

## Migration Checklist
- Run the SQL (extension + generated column + GIN index).
- Verify index creation finished (`CREATE INDEX CONCURRENTLY` can take time).
- Deploy code change that uses `search_text ILIKE`.

## Validation
- Run `EXPLAIN ANALYZE` before/after with a representative keyword.
- Measure response time and rows scanned.
- Verify result relevance is still acceptable.

## Risks / Notes
- Trigram indexes require DB extension privileges.
- Trigram indexes can be large; expect extra disk usage and slower writes on `news_summaries`.
- If the dataset is small, the index might be less noticeable.

## Rollback
- Keep old query as a fallback (feature flag or simple revert).
