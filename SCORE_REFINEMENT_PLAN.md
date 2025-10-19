# Score Refinement Plan

## Background
- Current workflow stores a single `score` on `primary_articles` / `news_summaries`, produced entirely by the LLM relevance model.
- We need to introduce rule-based bonuses (e.g., keyword hits like "北京市委教育工委", "北京市教育委员会") without breaking downstream thresholds, ordering, or historical data.

## Objectives
- Preserve the existing `score` as the final, comprehensive value used by summarise/export steps.
- Store both the base LLM relevance score and any additive bonuses for transparency and tuning.
- Keep downstream consumers backward-compatible while exposing the richer breakdown.

## Proposed Data Model Changes
1. **Schema**
   - Add `raw_relevance_score` (`numeric(6,3)` or `integer`) to `primary_articles` and `news_summaries`.
   - Add `keyword_bonus_score` with the same type.
   - (Optional) Add `score_details jsonb` for storing matched rules, bonus amounts, timestamps.
   - Write a migration (SQL or alembic equivalent) and update `database/schema.sql`. Ensure indexes referencing `score` remain untouched.
2. **Backfill**
   - Default existing rows: `raw_relevance_score = score`, `keyword_bonus_score = 0`.
   - For `score_details`, initialise as `NULL`.

## Application Layer Updates
1. **Domain Models**
   - Extend `PrimaryArticleForScoring`, `PrimaryArticleForSummarizing`, `ExportCandidate` with the new fields.
2. **Database Adapter**
   - Update `fetch_primary_articles_for_scoring`, `update_primary_article_scores`, and `upsert_news_summaries_from_primary` to read/write the new columns.
   - Ensure keyword arrays continue to deduplicate, but now also pass to the rule engine for bonuses.
3. **Configuration**
   - Introduce a structured configuration (YAML/JSON/env) for keyword bonus rules:
     - Exact string matches.
     - Optional case sensitivity/regex or weighting (future-proof).
   - Expose via `src/config.py` so workers can read without code changes per keyword tweak.

## Scoring Workflow Enhancements
1. **Score Worker (`src/workers/score.py`)**
   - Compute `raw_relevance_score` using existing LLM call (rename variable for clarity).
   - Apply keyword bonus logic on `content_markdown` (and optionally `title`, `keywords`).
   - Calculate `final_score = raw_relevance_score + keyword_bonus_score` (no upper bound).
   - Persist all three values plus structured `score_details` (e.g., list of `{rule_id, bonus}`); default to `{}` when no rules fire.
   - Update logging to differentiate LLM score vs bonus vs final.
2. **Promotion Logic**
   - Continue threshold checks against `raw_relevance_score >= 60` (current behaviour).
   - Ensure data synced to `news_summaries` includes the extra fields.

## Downstream Consumers
- **Summarise Worker**: Confirm it reads `score` as before; optionally surface bonuses if needed for prompts.
- **Export Pipeline**: Maintain ordering by `score`. Optionally display breakdown in internal reports.
- **Metrics (`scripts/pipeline_metrics.py`)**: Update to capture new columns for dashboards or alerts.

## Testing & Validation
1. Add unit tests for the new keyword bonus utility (positive and negative matches).
2. Extend score worker tests to validate persistence of all fields and bonus behaviour.
3. Backfill script dry-run on staging data; verify aggregates before/after.
4. Regression run of full pipeline (`crawl -> score -> summarize -> export`) with sample data.

## Rollout Steps
1. Ship schema migration; apply to staging, then production.
2. Deploy updated code + configuration.
3. Execute backfill to seed new columns.
4. Monitor scoring logs/metrics for anomalies; adjust bonus weights as needed.
5. Update documentation (`README`, troubleshooting) to describe new scoring breakdown and config knobs.

## Score Details Payload (optional)
- Purpose: provide traceability for why a final score was assigned, without bloating the main columns.
- Recommended JSON structure (keep keys short to limit storage):
  ```json
  {
    "raw_relevance_score": 58,
    "keyword_bonus_score": 120,
    "matched_rules": [
      {"rule_id": "keyword:北京市委教育工委", "label": "Beijing Municipal Party Committee", "bonus": 100},
      {"rule_id": "keyword:北京市教育委员会", "label": "Beijing Municipal Education Commission", "bonus": 20}
    ],
    "notes": "Promotion uses raw_relevance_score threshold (>= 60)."
  }
  ```
- Populate as `{}` when no rules match to keep JSON access consistent; enrich with matched rule data when available.
- Fields can evolve later to capture penalties, decay factors, or LLM metadata as needed.

## Decisions & Notes
- Per-source bonuses: not required for the current iteration; we focus solely on keyword-based adders.
- Promotion threshold: stay with `raw_relevance_score >= 60`. If we ever gate on `final_score`, only the worker logic needs to flip.
- `score_details`: optional but recommended when investigating rule effects; it documents which rules fired and their contributions.

## Action Checklist
- [ ] Update database schema and migrations for `raw_relevance_score`, `keyword_bonus_score`, and `score_details`.
- [ ] Extend domain models (`PrimaryArticleForScoring`, `PrimaryArticleForSummarizing`, `ExportCandidate`) with new fields.
- [ ] Adjust database adapter read/write logic for the additional columns.
- [ ] Implement keyword bonus calculation and `score_details` persistence in `src/workers/score.py`.
- [ ] Introduce configuration surface for keyword bonus rules.
- [ ] Ensure `news_summaries` promotion syncs new fields and respects raw-score threshold.
- [ ] Update tests (unit/integration) to cover new scoring breakdown.
- [ ] Refresh documentation and pipeline metrics to reflect the refined scoring model.
