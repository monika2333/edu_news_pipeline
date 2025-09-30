## Progress Snapshot (2025-09-30)

- Worker interfaces/logging unified: shared helpers in `src/workers/__init__.py`; `summarize`/`score`/`export_brief` now use `run(limit=...)` with consistent metrics and error reporting.
- CLI coverage extended: `src/cli/main.py` exposes concurrency/limit toggles, keywords path, export skip/history toggles; `run_pipeline.py` continues to forward to the parser.
- Docs & smoke tests refreshed: README now points to the CLI-first workflow and `tests/test_smoke.py` validates parser/worker imports.
- Core skeleton live: `src/config.py` centralises env/config; `src/domain` hosts states, scoring stubs, and shared dataclasses.
- Supabase adapter rebuilt (`src/adapters/db_supabase.py`) plus SiliconFlow summarise/score adapters; pipelines now share one client path.
- Workers refactored: `summarize`, `score`, `export_brief` consume adapters, support concurrency, and replace the old tools (which now warn and forward).

## Next Actions

- Remove the deprecated `tools/*.py` shims once the new CLI workflows are fully validated.

