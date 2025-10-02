## Progress Snapshot (2025-09-30)

- Worker interfaces/logging unified: shared helpers in `src/workers/__init__.py`; `summarize`/`score`/`export_brief` now use `run(limit=...)` with consistent metrics and error reporting.
- Core skeleton live: `src/config.py` centralises env/config; `src/domain` hosts states, scoring stubs, and shared dataclasses.
- Supabase adapter rebuilt (`src/adapters/db_supabase.py`) plus SiliconFlow summarise/score adapters; pipelines now share one client path.
- Workers refactored: `summarize`, `score`, `export_brief` consume adapters, support concurrency, and replace the old tools (which now warn and forward).

## Completed Actions (2025-10-02)

- Extended the CLI entry point to expose crawl/summarize/score/export with consistent options; confirmed `run_pipeline.py` forwards to the new parser.
- Refreshed documentation with scheduling guidance and the deprecation timeline for legacy tooling.
- Added a lightweight CLI smoke test (`tests/test_cli_parser.py`) to ensure subcommands stay registered.

## Next Actions

- Monitor CLI/test usage and retire the legacy `tools/` shims after the 2025-10-31 cutoff.
