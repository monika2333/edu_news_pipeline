# Applying Supabase Schema Updates

This project keeps Supabase DDL under `supabase/`. After pulling new changes you can apply the latest tables/indexes with the Supabase CLI.

## Prerequisites
- [Supabase CLI](https://supabase.com/docs/reference/cli/installation) installed locally (`npm install -g supabase` on Windows works).
- Environment variables/config that point the CLI at your project (e.g. run `supabase login` once, and ensure `.supabase/config.toml` has `project_ref` / `db.host`, or export `SUPABASE_ACCESS_TOKEN` / `SUPABASE_DB_URL`).

## Steps
1. Install/update the CLI (if needed):
   ```powershell
   npm install -g supabase
   ```
2. Authenticate the CLI:
   ```powershell
   supabase login
   ```
3. From the repository root run:
   ```powershell
   supabase db push
   ```
   This will apply the latest migration (`supabase/migrations/20251001151834_add_pipeline_run_tables.sql`) and update the remote schema.

If you prefer applying manually, execute the SQL in `supabase/migrations/20251001151834_add_pipeline_run_tables.sql` against your Postgres database.
