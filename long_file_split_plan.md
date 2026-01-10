# Long File Split Plan

## Scope (current hotspots)
- src/adapters/db_postgres.py (~2765 lines)
- src/workers/crawl_sources.py (~1284 lines)
- src/workers/export_brief.py:run (~260 lines)
- src/workers/summarize.py:run (~179 lines)
- src/workers/hash_primary.py:run (~161 lines)
- src/workers/external_filter.py:run (~141 lines)
- src/workers/score.py:run (~123 lines)
- src/adapters/http_*.py: list_items/fetch_detail/crawl (~80-95 lines)

## Principles
- Prefer small helper functions over deep class hierarchies.
- Keep imports stable; add new modules and re-export from old entry points; db_postgres.py must re-export the full public surface via `__all__`.
- Move only cohesive blocks (query building, row mapping, ranking, batching).
- Avoid renaming public functions unless a wrapper is kept.
- Keep behavior identical (no logic changes) and public APIs stable.
- New modules must include `from __future__ import annotations`, `__all__`, and follow import ordering.
- db_postgres.py must re-export the full public surface via `__all__`.
- Aim for file size <= 500 lines (except crawl_sources.py), but prioritize clear boundaries over strict limits.
- Avoid over-splitting: keep related helpers in the same file unless size or clarity forces a split.
- Enforce one-way deps: db_postgres.py (facade) -> db_postgres_core.py -> domain modules -> db_postgres_shared.py.
- Core owns connection/transaction lifecycle and passes conn/cursor into domain functions.
- Domain functions accept conn/cursor and do not import db_postgres_core.py.
- Domain modules must not import each other; share code via db_postgres_shared.py.

Dependency diagram (one-way; no domain-to-domain imports, no core imports in domain modules):
db_postgres.py
  -> db_postgres_core.py (PostgresAdapter, connection)
    -> db_postgres_ingest.py
      -> db_postgres_shared.py
    -> db_postgres_news_summaries.py
      -> db_postgres_shared.py
    -> db_postgres_process.py
      -> db_postgres_shared.py
    -> db_postgres_manual_reviews.py
      -> db_postgres_shared.py
    -> db_postgres_export.py
      -> db_postgres_shared.py

## Granularity Guardrails
- Do not split db_postgres into more than 6 domain modules; shared helpers live in db_postgres_shared.py.
- Keep worker/console helpers in the same file unless a file exceeds ~600 lines.
- For HTTP adapters, keep only two layers (request + parse).
- For crawl_sources, keep a single file and only 3-4 helpers; no new helper module unless it grows beyond ~1600 lines.

## Proposed Splits

### 1) src/adapters/db_postgres.py
Problem: One file owns many unrelated queries and helpers.
Plan:
- Extract domain-specific modules:
  - src/adapters/db_postgres_core.py (PostgresAdapter, connection, cursor helpers, transaction)
  - src/adapters/db_postgres_shared.py (shared SQL fragments, row mapping, small utilities)
  - src/adapters/db_postgres_ingest.py (raw/filtered/primary upserts)
  - src/adapters/db_postgres_news_summaries.py (summary CRUD, search)
  - src/adapters/db_postgres_process.py (scoring, gating, pipeline metadata)
  - src/adapters/db_postgres_manual_reviews.py (manual reviews + clustering candidates)
  - src/adapters/db_postgres_export.py (export candidates, batches, history, recording)
- Keep src/adapters/db_postgres.py as a thin facade that imports/re-exports.
- Preserve function names so external call sites remain unchanged.
- Adopt option B: PostgresAdapter lives in db_postgres_core.py; db_postgres.py only re-exports.

### 2) src/workers/crawl_sources.py
Problem: Orchestration + per-source logic in one large run.
Plan:
- Keep a single file and split into only a few stages:
  - _prepare_sources_and_tasks()
  - _fetch_and_parse_items()
  - _persist_results_and_log()
- Keep crawl_sources.py run() as a thin orchestrator calling helpers.

### 3) Worker run() methods
Files: export_brief.py, summarize.py, hash_primary.py, external_filter.py, score.py
Plan:
- Extract discrete steps:
  - fetch rows
  - pre-validate / filter
  - per-item processing
  - bulk write
  - summary logging
- Target: run() <= 80-100 lines, each helper <= 80 lines.

### 4) HTTP adapter modules
Files: http_*.py
Plan:
- Split network request from parsing:
  - _fetch_list_response()
  - _parse_list_items()
  - _fetch_detail()
  - _parse_detail()
- Keep list_items()/fetch_detail()/crawl() as thin wrappers.

## Execution Order (low risk first)
- [ ] Split worker run() helpers (no API changes)
- [ ] Split crawl_sources.py helpers (no API changes)
- [ ] Split HTTP adapters (keep function names intact)
- [ ] Split db_postgres.py last (largest surface area)

## Validation Checklist
- [ ] Run targeted tests related to manual_filter and workers
- [ ] Run at least one pipeline step end-to-end (crawl/score/summarize)
- [ ] Spot-check console endpoints (manual_filter candidates/export)
- [ ] Smoke-check db_postgres SQL paths for manual reviews/export/news summaries
- [ ] Verify no import cycles introduced
