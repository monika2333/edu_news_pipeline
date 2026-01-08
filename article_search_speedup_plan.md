# Article Search Speedup Plan (Keywords-Only)

## Goal
- Improve `/articles/search` responsiveness by narrowing to keywords-only search.
- Remove unused filters from UI and API while keeping results quality acceptable.

## Current Bottleneck (Observed)
- `ILIKE` search across `title`, `content_markdown`, and `llm_summary` in `news_summaries` forces large sequential scans.
- `content_markdown` is large; it is the biggest contributor to slow scans.

## Plan

### Phase 1: Quick Win (No DB Migrations)
- UI: simplify search form to keywords input + page size only.
- API: accept only `q`, `page`, `limit` (other filters ignored or deprecated).
- Query: remove `content_markdown` from the `ILIKE` clause; search only `title` + `llm_summary`.
- Expected impact: faster scans and less memory pressure with minimal change risk.

### Phase 2: Proper Indexing (Recommended)
Option A: Full-text search (fastest for keywords)
- Add a `tsvector` (generated column or expression index) on `title` + `llm_summary`.
- Query with `plainto_tsquery` or `websearch_to_tsquery`.

Option B: Trigram index (good for partial matches)
- Enable `pg_trgm`, add `GIN` or `GiST` trigram indexes on `title` and `llm_summary`.
- Keep `ILIKE` while benefiting from index acceleration.

### Phase 3: Cleanup
- Remove deprecated filter fields from `/articles/search` UI and docs.
- Update the prompt doc to reflect keywords-only search.

## Validation
- Run `EXPLAIN ANALYZE` before/after with a representative keyword.
- Measure response time and rows scanned.
- Verify result relevance is still acceptable.

## Risks / Notes
- Full-text search can change matching behavior vs `ILIKE`.
- Trigram indexes require DB extension privileges.
- If the dataset is small, Phase 1 might be enough.

## Rollback
- Keep old query as a fallback (feature flag or simple revert).
