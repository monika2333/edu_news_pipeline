# Beijing Internal Positive Scoring Plan

1. Analyze current external_filter flow and note that Beijing positives marked ready_for_export skip scoring.
2. Modify Beijing gate to send positive Beijing articles into the external filter queue instead of ready_for_export, resetting external filter counters.
3. Update external_filter worker to handle Beijing positives: select prompt/threshold per category, log category context, and store category info in raw payload.
4. Extend configuration for distinct thresholds or prompts (e.g., INTERNAL_FILTER_THRESHOLD) and ensure candidates carry sentiment/Beijing flags.
5. Add tests/documentation: verify new routing and scoring via unit tests, update README guidance, and ensure export ordering uses the new score.

