# Local Postgres Migration Plan

## Objective
- Replace the current Supabase dependency with a locally hosted PostgreSQL database for development and pipeline execution.

## Guiding Principles
- Maintain feature parity and data model compatibility with the existing Supabase schema.
- Keep workers and downstream components stable by preserving adapter method signatures where possible.
- Provide clear setup instructions so developers can bootstrap the local environment quickly.

## Phase 1 - Environment Preparation
1. Decide on the preferred local Postgres distribution (Docker container vs. native install).
2. Provision a database instance (e.g., Docker `postgres:16`) with persistent storage.
3. Load `supabase/schema.sql` into the new database to mirror the existing schema.
4. Define and document the canonical local connection string (host, port, database, user, password).

## Phase 2 - Configuration Updates
1. Extend `src/config.py` to surface first-class Postgres settings (e.g., `POSTGRES_HOST`, `POSTGRES_DB`).
2. Update `.env.local` template values to default to the local Postgres instance and remove unnecessary Supabase keys.
3. Document environment variable changes in `README.md` and any onboarding guides under `docs/`.

## Phase 3 - Adapter Implementation
1. Introduce `src/adapters/db_postgres.py` built on `psycopg`, mirroring the public API of `SupabaseAdapter`.
2. Extract shared logic where practical to reduce duplication between adapters during the transition.
3. Ensure connection pooling / session handling is appropriate for worker concurrency.

## Phase 4 - Worker & Pipeline Integration
1. Update workers (e.g., `src/workers/summarize.py`, `src/workers/score.py`) and related scripts to resolve the correct adapter based on configuration.
2. Adjust `src/adapters/http_toutiao.py` to perform inserts/updates using the Postgres adapter.
3. Provide a feature flag or toggle to switch between Supabase and Postgres during validation if needed.

## Phase 5 - Validation & Testing
1. Create lightweight smoke tests or scripts that exercise the new adapter against a seeded local database.
2. Run existing workers end-to-end in a local environment to confirm data flow and logging.
3. Verify migrations/schema changes apply cleanly and indexes exist as expected.

## Phase 6 - Cleanup & Documentation
1. Remove deprecated Supabase-specific code paths once Postgres is confirmed stable.
2. Update developer documentation (README, `docs/`) to reference the local Postgres workflow exclusively.
3. Review and sanitize configuration files to ensure no Supabase secrets remain.

## Deliverables Checklist
- [x] Local Postgres instance running with Supabase schema applied.
- [x] Updated configuration module and `.env.local` values.
- [x] New Postgres adapter with parity coverage for existing data operations.
- [x] Workers and scripts using the local Postgres path.
- [x] Validation scripts/tests and updated documentation.

## Open Questions
- Should we maintain a Supabase fallback for production or remove it entirely?
- Do we need automated migration tooling for existing Supabase data, or will new pipelines start fresh?
- What seeding strategy (if any) is required to support local development fixtures?

