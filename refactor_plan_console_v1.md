# Console Refactor Plan (Scheme A)

Goal: reduce nested directories and keep each console feature in a tight, easy-to-find cluster, so changes do not require hopping across many folders.

Scope: console-only restructuring first. No behavior changes unless explicitly listed.

Non-goals:
- No API behavior changes.
- No DB schema changes.
- No CLI/worker refactors yet.

## Proposed structure (rooted in src/console)

- app.py
- security.py
- articles_routes.py
- articles_service.py
- articles_schemas.py
- exports_routes.py
- exports_service.py
- exports_schemas.py
- runs_routes.py
- runs_service.py
- runs_schemas.py
- manual_filter_routes.py
- manual_filter_service.py
- manual_filter_schemas.py
- web_routes.py
- web_templates/
  - landing.html
  - dashboard.html
  - search.html
  - manual_filter.html
- web_static/
  - css/
    - dashboard.css
  - js/
    - dashboard.js

Notes:
- One feature = 3 files (routes/service/schemas) + templates/static if needed.
- Keep app wiring in one place (`app.py`) and keep it small.

## Migration steps (incremental, low-risk)

1) Prepare a safe baseline
   - Ensure the current branch is clean (besides the new plan doc).
   - Optional: run `python -m pytest tests/test_manual_filter_service.py` before moving files.

2) Flatten the console folder
   - Move:
     - `src/console/routes/*.py` -> `src/console/*_routes.py`
     - `src/console/services/*.py` -> `src/console/*_service.py`
     - `src/console/schemas/*.py` -> `src/console/*_schemas.py`
     - `src/console/web/templates` -> `src/console/web_templates`
     - `src/console/web/static` -> `src/console/web_static`
   - Remove empty directories after moves: `routes/`, `services/`, `schemas/`, `web/`.

3) Update imports and module references
   - Update `src/console/app.py` to import from the new file names.
   - Update cross-imports inside services/routes to the new module paths.
   - Update any test references (notably `tests/test_manual_filter_service.py`).
   - Update any runtime entrypoints using console modules (e.g. `run_console.py`).

4) Fix static/template mounts
   - Update `app.mount("/static", ...)` to point at `src/console/web_static`.
   - Update `Jinja2Templates` path in `web_routes.py` to `src/console/web_templates`.

5) Run minimal tests
   - `python -m pytest tests/test_manual_filter_service.py`
   - If no failures, optionally run `python -m pytest -v`.

6) Smoke-check the console
   - `python run_console.py`
   - Open `/` and `/manual-filter` pages and verify template/static paths load.

## Suggested file mapping (old -> new)

- src/console/routes/articles.py -> src/console/articles_routes.py
- src/console/routes/exports.py -> src/console/exports_routes.py
- src/console/routes/runs.py -> src/console/runs_routes.py
- src/console/routes/health.py -> src/console/health_routes.py
- src/console/routes/manual_filter.py -> src/console/manual_filter_routes.py
- src/console/routes/web.py -> src/console/web_routes.py

- src/console/services/articles.py -> src/console/articles_service.py
- src/console/services/exports.py -> src/console/exports_service.py
- src/console/services/runs.py -> src/console/runs_service.py
- src/console/services/manual_filter.py -> src/console/manual_filter_service.py

- src/console/schemas/article.py -> src/console/articles_schemas.py
- src/console/schemas/export.py -> src/console/exports_schemas.py
- src/console/schemas/run.py -> src/console/runs_schemas.py

- src/console/web/templates -> src/console/web_templates
- src/console/web/static -> src/console/web_static

## Risk checklist

- Import paths in tests and routes: update all `from src.console.routes ...` and `from src.console.services ...`.
- `app.mount` and template paths will break if not updated.
- Keep the new filenames consistent: `*_routes.py`, `*_service.py`, `*_schemas.py`.

## Optional improvements after Scheme A (not part of this pass)

- Split `manual_filter_service.py` into smaller modules (cluster/export/decision/meta).
- Move export formatting logic into a shared module for console + worker reuse.
