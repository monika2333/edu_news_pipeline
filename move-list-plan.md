## Progress Snapshot (2025-09-30)

- Core skeleton live: `src/config.py` centralises env/config; `src/domain` hosts states, scoring stubs, and shared dataclasses.
- Supabase adapter rebuilt (`src/adapters/db_supabase.py`) plus SiliconFlow summarise/score adapters; pipelines now share one client path.
- Workers refactored: `summarize`, `score`, `export_brief` consume adapters, support concurrency, and replace the old tools (which now warn and forward).

## Next Actions

1. Standardise worker interfaces/logging (`src/workers/*`) so every runner exposes `run(limit=...)`, basic print hooks, and consistent error handling.
2. Extend CLI coverage in `src/cli/main.py` (ensure new options/flags are exposed, add help text, confirm `run_pipeline.py` forwards appropriately).
3. Refresh docs/tests: update README + scheduling notes, add a smoke script/test import, and document removal plan for deprecated `tools/` shims once verified.
