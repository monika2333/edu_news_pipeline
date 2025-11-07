# Negative External Scoring Plan

1. **Summarize worker routing** (`src/workers/summarize.py:135-144`): extend the sentiment routing logic so that non-Beijing negatives also enqueue into `pending_external_filter`, while Beijing-related items still enter `pending_beijing_gate` first. After gate confirmation, ensure Beijing negatives get routed onward to external scoring rather than skipping it.
2. **Beijing gate completion rules** (`src/adapters/db_postgres.py:990-1011`): replace the `positive_sentiment` shortcut with a sentiment-aware switch so both positive and negative Beijing articles can trigger `pending_external_filter`. Persist the eventual sentiment-based category into `external_importance_raw` to guide downstream processing.
3. **Candidate category granularity** (`src/domain/external_filter.py:39-40`): expand `candidate_category` to four buckets (internal/external × positive/negative) derived from `is_beijing_related` and `sentiment_label`, with a safe fallback when sentiment is missing.
4. **Prompt coverage** (`src/adapters/external_filter_model.py:15-34` + `docs/`): add dedicated prompt files for “京内负面”与“京外负面”打分，update `_DEFAULT_PROMPT_PATHS` and `_get_prompt_path` to resolve the four templates, and keep backward-compatible fallbacks if files are absent.
5. **Threshold configuration** (`src/config.py:213-219`): introduce `EXTERNAL_FILTER_NEGATIVE_THRESHOLD` and `INTERNAL_FILTER_NEGATIVE_THRESHOLD`, store them in `Settings`, and expose them to workers so `_score_candidate` can pull the exact limit for each category.
6. **Worker updates** (`src/workers/external_filter.py:24-198`): make sure `_score_candidate` passes the new category labels through to `call_external_filter_model`, feed the expanded threshold map, and write the category into `adapter.complete_external_filter`. Consider adding sentiment info to logs for easier debugging.
7. **Backfill / scripts sanity** (`scripts/backfill_external_filter.py` & CLI): confirm existing scripts either continue to work or get optional flags to repopulate negative items. Document any manual steps needed to requeue historic rows.
8. **Tests** (`tests/test_external_filter_worker.py`, optional summarize tests): add coverage for the new category logic, threshold selection, and prompt selection. If practical, craft a summarize-worker unit test to ensure negative sentiment paths enqueue correctly.
9. **Docs & README** (`README.md`, new prompt docs): describe the expanded external filter scope, list the new environment variables, and mention where to edit the four prompt files so operators know how to tune them.

## Implementation Checklist

- [x] Update summarize worker routing for negative external items.
- [x] Adjust Beijing gate completion logic to route negative Beijing items to external filter.
- [x] Expand candidate categories to cover internal/external × positive/negative.
- [x] Add negative-specific prompt templates and wire them into the adapter.
- [x] Introduce negative threshold env settings and expose through config.
- [ ] Update external filter worker to use new categories and thresholds.
- [ ] Review auxiliary scripts/backfills for compatibility.
- [ ] Extend test suite for new logic and prompts.
- [ ] Refresh README/docs to describe the expanded scoring and prompts.
