# Article Search Speedup Plan (Keywords Search + content_markdown)

## Goal
- Improve `/articles/search` responsiveness while keeping `content_markdown` in the keyword search scope.
- Keep the UI focused on keyword search + date range (remove unused filters).
- Reduce response payload by returning `llm_summary` only, and fetch `content_markdown` on demand.

## Current Bottleneck (Observed)
- `ILIKE` search across `title`, `content_markdown`, and `llm_summary` in `news_summaries` forces large sequential scans.
- `content_markdown` is large; it is the biggest contributor to slow scans.

## Plan

### Phase 1: UI + API Simplification
- UI (manual filter drawer + `/articles/search` page): simplify search form to keywords + date range + page size.
- API: accept only `q`, `page`, `limit`, `start_date`, `end_date` (other filters removed).
- Response payload: return `llm_summary` only; add a "load content" action to fetch `content_markdown` by article id.

### Phase 2: Trigram Index on a Combined Search Expression (Chosen)
- Use a combined search expression (title + llm_summary + content_markdown).
- Add a trigram GIN index on the expression.
- Update the query to use a single `ILIKE` against the same expression.

Suggested SQL:
```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY IF NOT EXISTS news_summaries_search_expr_trgm
ON news_summaries
USING gin (
    (coalesce(title, '') || ' ' || coalesce(llm_summary, '') || ' ' || coalesce(content_markdown, ''))
    gin_trgm_ops
);
```

Query change (example):
```sql
WHERE (
  coalesce(title, '') || ' ' ||
  coalesce(llm_summary, '') || ' ' ||
  coalesce(content_markdown, '')
) ILIKE %s
```

### Phase 3: Cleanup
- Remove deprecated filter fields from `/articles/search` UI and docs (source/sentiment/status).
- Update the prompt doc to reflect keywords + date range search.

## Code Touchpoints
- `src/adapters/db_postgres_news_summaries.py` (`search_news_summaries`): replace the three-column `ILIKE` clause with the combined expression `ILIKE`.
- `src/adapters/db_postgres_news_summaries.py`: add a lightweight fetch for `content_markdown` by `article_id` (on-demand content).
- `src/adapters/db_postgres_core.py`: pass-through stays the same, but ensure it exposes the updated search query.
- `src/console/articles_service.py`: remove unused filter arguments (`sources`, `sentiments`, `statuses`), keep date range.
- `src/console/articles_routes.py`: drop unused query params, keep `q`, `page`, `limit`, `start_date`, `end_date`.
- `src/console/articles_routes.py`: add a content fetch endpoint (e.g., `/api/articles/{article_id}/content`).
- `src/console/web_routes.py`: simplify `/articles/search` form handling to only accept keywords + date + pagination.
- `src/console/web_templates/search.html`: remove filter inputs for source/sentiment/status (keep date).
- `src/console/web_templates/search.html`: add "load content" action that fetches content on demand.
- `src/console/web_templates/manual_filter.html`: keep only the keywords + date search UI inside the drawer.
- `src/console/web_static/js/manual_filter/search_drawer.js`: keep query + pagination + date parameters, remove extra filters and add on-demand content fetch.
- `README.md`: update the `/articles/search` description to "keywords + date range".

## Migration Checklist
- Run the SQL (extension + expression GIN index).
- Verify index creation finished (`CREATE INDEX CONCURRENTLY` can take time).
- Deploy code change that uses the combined expression `ILIKE`.

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
