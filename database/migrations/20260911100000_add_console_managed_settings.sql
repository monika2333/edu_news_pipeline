-- migrate:up
create table public.app_settings (
    section text primary key,
    value jsonb not null,
    version integer not null default 1 check (version > 0),
    updated_at timestamptz not null default now(),
    updated_by_user_id uuid references public.console_users(id) on delete restrict
);

create table public.crawl_accounts (
    id uuid primary key default gen_random_uuid(),
    source text not null,
    normalized_identifier text not null,
    original_input text not null,
    profile_url text not null,
    display_name text,
    enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    created_by_user_id uuid references public.console_users(id) on delete restrict,
    updated_by_user_id uuid references public.console_users(id) on delete restrict,
    constraint crawl_accounts_source_identifier_unique
        unique (source, normalized_identifier)
);

create index crawl_accounts_enabled_source_idx
    on public.crawl_accounts (source, created_at, id)
    where enabled;

alter table public.pipeline_runs
    add column config_snapshot jsonb;

-- migrate:down
alter table public.pipeline_runs
    drop column config_snapshot;

drop table public.crawl_accounts;
drop table public.app_settings;
