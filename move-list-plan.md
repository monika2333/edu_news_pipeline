## Progress Snapshot (2025-09-30)

- Worker interfaces/logging unified: shared helpers in `src/workers/__init__.py`; `summarize`/`score`/`export_brief` now use `run(limit=...)` with consistent metrics and error reporting.
- Core skeleton live: `src/config.py` centralises env/config; `src/domain` hosts states, scoring stubs, and shared dataclasses.
- Supabase adapter rebuilt (`src/adapters/db_supabase.py`) plus SiliconFlow summarise/score adapters; pipelines now share one client path.
- Workers refactored: `summarize`, `score`, `export_brief` consume adapters, support concurrency, and replace the old tools (which now warn and forward).

## Next Actions

1. Extend CLI coverage in `src/cli/main.py` (ensure new options/flags are exposed, add help text, confirm `run_pipeline.py` forwards appropriately).
2. Refresh docs/tests: update README + scheduling notes, add a smoke script/test import, and document removal plan for deprecated `tools/` shims once verified.

