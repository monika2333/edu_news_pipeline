-- migrate:up
create table if not exists public.shift_review_finalization_batches (
    id uuid primary key default gen_random_uuid(),
    shift_id uuid not null references public.duty_shifts(id) on delete restrict,
    report_type text not null,
    finalized_by_user_id uuid not null
        references public.console_users(id) on delete restrict,
    finalized_at timestamptz not null default now(),
    constraint shift_review_finalization_batches_report_type_check
        check (report_type in ('zongbao', 'wanbao'))
);

create index if not exists shift_review_finalization_batches_shift_idx
    on public.shift_review_finalization_batches
        (shift_id, report_type, finalized_at);

alter table public.shift_reviews
    add column if not exists finalized_batch_id uuid
        references public.shift_review_finalization_batches(id) on delete restrict;

alter table public.shift_reviews
    add column if not exists finalized_rank integer;

alter table public.shift_reviews
    add constraint shift_reviews_finalized_rank_check
        check (finalized_rank is null or finalized_rank > 0);

alter table public.shift_reviews
    add constraint shift_reviews_finalization_pair_check
        check (
            (finalized_batch_id is null and finalized_rank is null)
            or (finalized_batch_id is not null and finalized_rank is not null)
        );

create index if not exists shift_reviews_finalized_batch_idx
    on public.shift_reviews (finalized_batch_id, finalized_rank)
    where finalized_batch_id is not null;

create index if not exists shift_reviews_current_selected_idx
    on public.shift_reviews (shift_id, report_type, rank)
    where decision = 'selected' and finalized_batch_id is null;

-- migrate:down
drop index if exists public.shift_reviews_current_selected_idx;
drop index if exists public.shift_reviews_finalized_batch_idx;

alter table public.shift_reviews
    drop constraint if exists shift_reviews_finalization_pair_check;

alter table public.shift_reviews
    drop constraint if exists shift_reviews_finalized_rank_check;

alter table public.shift_reviews
    drop column if exists finalized_rank;

alter table public.shift_reviews
    drop column if exists finalized_batch_id;

drop index if exists public.shift_review_finalization_batches_shift_idx;
drop table if exists public.shift_review_finalization_batches;
