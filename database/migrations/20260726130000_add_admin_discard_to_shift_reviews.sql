-- migrate:up
alter table public.shift_reviews
    add column if not exists admin_discarded_at timestamptz;

alter table public.shift_reviews
    add column if not exists admin_discarded_by_user_id uuid
        references public.console_users(id) on delete restrict;

create index if not exists shift_reviews_admin_discarded_idx
    on public.shift_reviews (shift_id, admin_discarded_at desc)
    where admin_discarded_at is not null;

-- migrate:down
drop index if exists public.shift_reviews_admin_discarded_idx;

alter table public.shift_reviews
    drop column if exists admin_discarded_by_user_id;

alter table public.shift_reviews
    drop column if exists admin_discarded_at;
