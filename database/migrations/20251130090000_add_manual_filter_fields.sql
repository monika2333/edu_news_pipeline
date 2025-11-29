begin;

alter table if exists public.news_summaries
    add column if not exists manual_status text default 'pending',
    add column if not exists manual_summary text,
    add column if not exists manual_score numeric(6,3),
    add column if not exists manual_notes text,
    add column if not exists manual_decided_by text,
    add column if not exists manual_decided_at timestamptz;

update public.news_summaries
set manual_status = 'pending'
where manual_status is null;

alter table if exists public.news_summaries
    alter column manual_status set not null;

create index if not exists news_summaries_manual_status_idx
    on public.news_summaries (manual_status);

commit;
