# Beijing Internal Positive Scoring Plan

## Goals
- Apply an importance score to Beijing internal positive articles similar to external positives.
- Reuse the external filter worker so operational tooling stays consistent.
- Preserve existing flows for non-positive or external articles.

## Constraints & Considerations
- Beijing gate currently promotes confirmed positives straight to `ready_for_export`; we must queue them for scoring instead.
- External filter prompt and threshold may need tuning for internal vs external; design must support separate configuration.
- Avoid regressions in export ordering: ensure Beijing positives use the new score.
- Provide observability for the new path (logs, raw payload markers).

## Implementation Steps

### 1. Data flow adjustments
1. Update `PostgresAdapter.complete_beijing_gate`:
   - When `is_beijing_related=True` and underlying sentiment is positive (from candidate), set
     - `status = 'pending_external_filter'`
     - `external_importance_status = 'pending_external_filter'`
   - Ensure `external_filter_fail_count = 0` and `external_filter_attempted_at = NULL` so the worker retries fresh.
   - Preserve existing behaviour for negative/neutral or rerouted cases.
   - Include a category marker in `external_importance_raw` (e.g., `{"category": "internal"}`) if prior data exists.
2. Ensure `BeijingGateCandidate` conveys the sentiment label (already present) so the gate logic can check positivity before routing.

### 2. External filter worker enhancements
1. In `ExternalFilterCandidate`, confirm fields `sentiment_label` and `is_beijing_related` are available (already present); add helper `candidate_category = 'internal' if candidate.is_beijing_related else 'external'`.
2. Adjust `_score_candidate` (or a wrapper) to accept `category` so we can:
   - Choose prompt (default reuse existing; optionally load `docs/internal_importance_prompt.md` if provided).
   - Apply category-specific thresholds (`internal_threshold`, `external_threshold`).
3. Modify `call_external_filter_model` invocation to pass category context:
   - Update prompt builder to take category and optionally add section name or guidelines.
   - Log entries with category prefix (e.g., `INTERNAL OK article_id...`).
4. In `adapter.complete_external_filter`, include category in raw payload for visibility (`"category": category`).

### 3. Configuration updates
1. Extend `Settings`:
   - Add `internal_filter_threshold` (env: `INTERNAL_FILTER_THRESHOLD`, fallback to existing `external_filter_threshold`).
   - Optional: allow overriding prompt path (e.g., `INTERNAL_FILTER_PROMPT_PATH`) for future use.
2. Wire new settings into worker functions so thresholds and prompt selection honour environment overrides.
3. Document new environment variables in README and `.env.example` (if present).

### 4. Testing and validation
1. Unit tests:
   - `tests/test_llm_beijing_gate.py`: ensure a positive Beijing article now sets status to `pending_external_filter`.
   - `tests/test_external_filter_worker.py` (create if absent): mock adapter/model to verify category-specific thresholds and raw payload content.
2. Integration smoke (manual): run `python -m src.workers.external_filter --limit 1` with fixture data to ensure the flow works.
3. Verify export ordering still sorts Beijing positives by `external_importance_score` once populated.

### 5. Documentation and rollout
1. Update README "External Filter" section to mention it covers both Jingnei and Jingwai positives after the change.
2. Add release notes or migration tips: rerun the external filter worker to backfill existing Beijing positives if needed (provide script or instructions).
3. Communicate configuration changes to the team.

## Open Questions
- Do we need a distinct prompt for internal positives? (If yes, specify path and content.)
- Should neutral Beijing articles also receive a score? Currently they stay `ready_for_export`; confirm desired behaviour.
- Are there reporting dashboards that need new metrics?

