# Split LLM Providers – Checklist

Goal: keep most workers on OpenRouter while routing `summarize` worker (and optionally related calls) to SiliconFlow.

## 1. Config & Settings
- [ ] Extend `Settings` in `src/config.py` with dedicated summary provider fields (API key, base URL, optional headers, timeout overrides, enable_thinking).
- [ ] Load new env vars (e.g., `SUMMARY_LLM_API_KEY`, `SUMMARY_LLM_BASE_URL`, `SUMMARY_LLM_TIMEOUT_*`) with sensible fallbacks to existing global `LLM_*` values.
- [ ] Update `.env.local` example to include the new summary-specific variables and notes about precedence.
- [ ] Document new env vars in `README.md` (configuration table + narrative).
- [ ] Add `SUMMARY_CONCURRENCY` config (defaults to global `CONCURRENCY`) to cap summarize worker threads separately.

## 2. Adapter Changes
- [ ] Update `src/adapters/llm_summary.py` to use the summary-specific settings and headers.
- [ ] Decide whether sentiment/source detection (triggered after summary) should also use SiliconFlow; if yes, wire them to the summary provider or add separate toggles.
- [ ] Keep other adapters (score, external_filter, Beijing gate) on the global LLM config.

## 3. Timeout / Thinking Behavior
- [ ] Ensure summary provider honours its own timeout + thinking flag (`SUMMARY_LLM_ENABLE_THINKING`), with fallback to global values.
- [ ] Verify error messages reference the correct env vars when missing.

## 4. Testing & Validation
- [ ] Run `python -m compileall src` (quick smoke) after code changes.
- [ ] Dry-run `python -m src.cli.main summarize --limit 1` (if credentials available) to confirm SiliconFlow path works; otherwise, describe manual test steps.
- [ ] Verify other workers (`score`, `external-filter`) still resolve OpenRouter config.

## 5. Cleanup / Delivery
- [ ] Update the new checklist documenting what was implemented/left TODO.
- [ ] Summarize key changes + instructions for configuring both providers.
