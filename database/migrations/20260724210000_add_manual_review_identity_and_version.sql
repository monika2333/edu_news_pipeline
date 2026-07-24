-- migrate:up
alter table public.manual_reviews
    add column if not exists decided_by_user_id uuid
        references public.console_users(id) on delete restrict;

alter table public.manual_reviews
    add column if not exists version integer not null default 1;

alter table public.manual_reviews
    add constraint manual_reviews_version_check check (version > 0);

-- migrate:down
alter table public.manual_reviews
    drop constraint if exists manual_reviews_version_check;

alter table public.manual_reviews
    drop column if exists version;

alter table public.manual_reviews
    drop column if exists decided_by_user_id;
