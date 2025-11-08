# Split LLM Providers – Checklist

Goal: keep most workers on OpenRouter while routing `summarize` worker (and optionally related calls) to SiliconFlow.

## 1. Config & Settings
- [x] Extend `Settings` in `src/config.py` with dedicated summary provider fields (API key, base URL, optional headers, timeout overrides, enable_thinking).
- [x] Load new env vars (e.g., `SUMMARY_LLM_API_KEY`, `SUMMARY_LLM_BASE_URL`, `SUMMARY_LLM_TIMEOUT_*`) with sensible fallbacks to existing global `LLM_*` values.
- [x] Update `.env.local` example to include the new summary-specific variables and notes about precedence.
- [x] Document new env vars in `README.md` (configuration table + narrative).
- [x] Add `SUMMARY_CONCURRENCY` config (defaults to global `CONCURRENCY`) to cap summarize worker threads separately.

## 2. Adapter Changes
- [x] Update `src/adapters/llm_summary.py` to use the summary-specific settings and headers.
- [x] Decide whether sentiment/source detection (triggered after summary) should also use SiliconFlow; if yes, wire them to the summary provider or add separate toggles.
- [x] Keep other adapters (score, external_filter, Beijing gate) on the global LLM config.

## 3. Timeout / Thinking Behavior
- [x] Ensure summary provider honours its own timeout + thinking flag (`SUMMARY_LLM_ENABLE_THINKING`), with fallback to global values.
- [x] Verify error messages reference the correct env vars when missing.

## 4. Testing & Validation
- [x] Run `python -m compileall src` (quick smoke) after code changes.
- [x] Dry-run `python -m src.cli.main summarize --limit 1` (if credentials available) to confirm SiliconFlow path works; otherwise, describe manual test steps. *(Result: no pending summaries; command exercised new config without errors.)*
- [x] Verify other workers (`score`, `external-filter`) still resolve OpenRouter config. *(Ran `python -m src.cli.main score --limit 1`, completed successfully via OpenRouter.)*

## 5. Cleanup / Delivery
- [x] Update the new checklist documenting what was implemented/left TODO. *(Summary concurrency wired into `src/workers/summarize.py`; README now documents the split.)*
- [x] Summarize key changes + instructions for configuring both providers. *(README “LLM Provider Configuration” section added; final PR summary can reference it.)*
