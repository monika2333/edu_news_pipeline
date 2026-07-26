-- migrate:up
alter table public.console_users
    add column if not exists deleted_at timestamptz;

-- migrate:down
alter table public.console_users
    drop column if exists deleted_at;
