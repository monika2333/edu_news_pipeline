# Console Dashboard Development Plan

## Goals
- Provide a lightweight control panel to monitor pipeline exports and trigger runs manually.
- Reuse the existing Supabase/Postgres data store for run metadata while keeping room for future on-prem migration.
- Keep first iteration simple (API + minimal Jinja2 UI), with clear extension points for authentication, scheduling, and notifications.

## Target Stack
- **Backend**: FastAPI (serves REST endpoints + server-rendered pages via Jinja2)
- **Data Access**: Supabase client for run status + export metadata (introduce SQLAlchemy/SQLModel later when moving to local Postgres).
- **Task Triggering**: Call existing pipeline execution script; Windows Task Scheduler remains primary periodic runner. Add APScheduler/RQ later if needed.
- **Auth**: Initial token/basic auth via environment config, with plan to expand.

## High-Level Architecture
- `src/console/app.py` creates FastAPI app, mounts routes, configures templates.
- `src/console/routes/` exposes endpoints: list run history, latest export, trigger manual run, health check.
- `src/console/services/` encapsulates logic for interacting with pipeline runner, Supabase metadata, auth.
- `scripts/run_pipeline_once.py` offers CLI entry for single run (used by both Task Scheduler and manual trigger).
- Static/template assets stored under `src/console/web/` (templates, CSS).

## Supabase Metadata
- Tables `pipeline_runs` and `pipeline_run_steps` defined in `supabase/schema.sql` capture run lifecycle, step timings, artifacts, and error summaries.
- Pipeline runner writes start/step/finish records; console will read from these tables for dashboards.

## Iteration Plan
1. **Preparation**
   - Extract pipeline execution into reusable function/script (`scripts/run_pipeline_once.py`).
   - Confirm Supabase table schema + update pipeline to write metadata consistently.
2. **Backend Skeleton**
   - Create FastAPI app with routing, config, and Supabase client wiring.
   - Implement authentication middleware (token/basic) reading from `.env`.
3. **Core Features**
   - `GET /runs` (list recent runs) + Jinja2 page.
   - `GET /exports/latest` (metadata + download link).
   - `POST /runs/trigger` (invoke pipeline script, return job id/status).
4. **UX Enhancements**
   - Build minimal dashboard template (responsive layout, status badges, trigger button).
   - Add run detail view/log excerpt.
5. **Operational Hardening**
   - Add structured logging + error handling around script execution.
   - Provide notification hooks (email/webhook placeholder).
   - Document deployment steps in README (service startup, Task Scheduler configuration).
   - Secure console endpoints with env-configurable auth (see docs/console_auth.md). 
6. **Future Extensions** (optional backlog)
   - Introduce SQLAlchemy/SQLModel + Alembic when migrating to Postgres.
   - Add APScheduler or RQ for queued tasks + retries.
   - Implement user management, HTTPS termination, API tokens per user.
   - Integrate push delivery (email/Slack) for export artifacts.

## Immediate Next Steps
- Review/confirm Supabase metadata schema.
- Refactor pipeline entry point into reusable script.
- Scaffold `src/console/` package and FastAPI app structure.


