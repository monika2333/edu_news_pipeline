-- Add pipeline run metadata tables
create table if not exists public.pipeline_runs (
    id uuid primary key default gen_random_uuid(),
    run_id text not null unique,
    status text not null,
    trigger_source text,
    plan jsonb not null default '[]'::jsonb,
    started_at timestamptz not null,
    finished_at timestamptz,
    steps_completed integer not null default 0,
    artifacts jsonb,
    error_summary text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.pipeline_run_steps (
    id uuid primary key default gen_random_uuid(),
    run_id text not null references public.pipeline_runs(run_id) on delete cascade,
    order_index integer not null,
    step_name text not null,
    status text not null,
    started_at timestamptz not null,
    finished_at timestamptz not null,
    duration_seconds numeric(12,3),
    error text,
    created_at timestamptz not null default now()
);

create index if not exists pipeline_run_steps_run_id_idx on public.pipeline_run_steps(run_id);
create index if not exists pipeline_run_steps_step_name_idx on public.pipeline_run_steps(step_name);
