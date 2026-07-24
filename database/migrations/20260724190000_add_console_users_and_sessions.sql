-- migrate:up
create extension if not exists pgcrypto;

create table if not exists public.console_users (
    id uuid primary key default gen_random_uuid(),
    username text not null,
    display_name text not null,
    password_hash text not null,
    role text not null,
    is_active boolean not null default true,
    password_changed_at timestamptz,
    last_login_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint console_users_username_not_blank check (btrim(username) <> ''),
    constraint console_users_display_name_not_blank check (btrim(display_name) <> ''),
    constraint console_users_role_check check (role in ('admin', 'duty_editor'))
);

create unique index if not exists console_users_username_lower_idx
    on public.console_users (lower(username));

create table if not exists public.console_user_sessions (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.console_users(id) on delete restrict,
    token_hash text not null,
    csrf_token_hash text not null,
    expires_at timestamptz not null,
    last_seen_at timestamptz,
    revoked_at timestamptz,
    created_at timestamptz not null default now(),
    constraint console_user_sessions_token_hash_unique unique (token_hash)
);

create index if not exists console_user_sessions_user_id_idx
    on public.console_user_sessions (user_id);

create index if not exists console_user_sessions_expires_at_idx
    on public.console_user_sessions (expires_at);

-- migrate:down
drop table if exists public.console_user_sessions;
drop table if exists public.console_users;
