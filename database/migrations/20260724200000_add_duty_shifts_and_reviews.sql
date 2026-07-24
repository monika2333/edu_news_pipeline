-- migrate:up
create table if not exists public.duty_schedules (
    id uuid primary key default gen_random_uuid(),
    weekday smallint not null,
    user_id uuid not null references public.console_users(id) on delete restrict,
    updated_at timestamptz not null default now(),
    constraint duty_schedules_weekday_check check (weekday between 0 and 6),
    constraint duty_schedules_weekday_unique unique (weekday)
);

create table if not exists public.duty_shifts (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references public.console_users(id) on delete restrict,
    starts_at timestamptz not null,
    ends_at timestamptz not null,
    cancelled_at timestamptz,
    notes text,
    created_by_user_id uuid references public.console_users(id) on delete restrict,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint duty_shifts_range_check check (ends_at > starts_at),
    constraint duty_shifts_starts_at_unique unique (starts_at)
);

create index if not exists duty_shifts_user_id_idx
    on public.duty_shifts (user_id);

create index if not exists duty_shifts_starts_at_idx
    on public.duty_shifts (starts_at desc);

create table if not exists public.shift_reviews (
    id uuid primary key default gen_random_uuid(),
    shift_id uuid not null references public.duty_shifts(id) on delete restrict,
    article_id text not null,
    created_by_user_id uuid not null references public.console_users(id) on delete restrict,
    updated_by_user_id uuid not null references public.console_users(id) on delete restrict,
    report_type text,
    decision text not null default 'pending',
    rank integer,
    excerpt_text text,
    edited_summary text,
    manual_llm_source text,
    notes text,
    version integer not null default 1,
    decided_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint shift_reviews_decision_check
        check (decision in ('pending', 'selected', 'backup', 'discarded')),
    constraint shift_reviews_report_type_check
        check (report_type is null or report_type in ('zongbao', 'wanbao')),
    constraint shift_reviews_version_check check (version > 0),
    constraint shift_reviews_rank_check check (rank is null or rank > 0),
    constraint shift_reviews_shift_article_unique unique (shift_id, article_id)
);

create index if not exists shift_reviews_shift_id_idx
    on public.shift_reviews (shift_id);

create index if not exists shift_reviews_article_id_idx
    on public.shift_reviews (article_id);

create index if not exists shift_reviews_created_by_idx
    on public.shift_reviews (created_by_user_id);

create index if not exists shift_reviews_updated_by_idx
    on public.shift_reviews (updated_by_user_id);

create table if not exists public.review_events (
    id bigserial primary key,
    actor_user_id uuid,
    action text not null,
    target_type text not null,
    target_id text,
    before_data jsonb,
    after_data jsonb,
    request_id text,
    created_at timestamptz not null default now()
);

create index if not exists review_events_created_at_idx
    on public.review_events (created_at desc);

create index if not exists review_events_target_idx
    on public.review_events (target_type, target_id);

create index if not exists review_events_actor_idx
    on public.review_events (actor_user_id);

-- migrate:down
drop table if exists public.review_events;
drop table if exists public.shift_reviews;
drop table if exists public.duty_shifts;
drop table if exists public.duty_schedules;
