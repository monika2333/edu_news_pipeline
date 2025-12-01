begin;

-- Ensure pgcrypto is available for gen_random_uuid
create extension if not exists pgcrypto;

create table if not exists public.manual_reviews (
    id uuid primary key default gen_random_uuid(),
    article_id text not null references public.news_summaries(article_id) on delete cascade,
    status text not null check (status in ('pending', 'selected', 'backup', 'discarded', 'exported')),
    summary text,
    rank double precision,
    notes text,
    score numeric(6,3),
    decided_by text,
    decided_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (article_id)
);

create index if not exists manual_reviews_pending_idx
    on public.manual_reviews (status, rank asc nulls last, article_id)
    where status = 'pending';

create index if not exists manual_reviews_status_idx
    on public.manual_reviews (status);

do $$
declare
    has_rank_column boolean;
begin
    select exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'news_summaries'
          and column_name = 'manual_rank'
    ) into has_rank_column;

    if has_rank_column then
        insert into public.manual_reviews (
            article_id,
            status,
            summary,
            rank,
            notes,
            score,
            decided_by,
            decided_at,
            created_at,
            updated_at
        )
        select distinct on (ns.article_id)
            ns.article_id,
            case when ns.manual_status = 'approved' then 'selected' else ns.manual_status end as status,
            ns.manual_summary,
            ns.manual_rank,
            ns.manual_notes,
            ns.manual_score,
            ns.manual_decided_by,
            ns.manual_decided_at,
            coalesce(ns.created_at, now()),
            coalesce(ns.updated_at, now())
        from public.news_summaries ns
        where (ns.manual_status in ('selected', 'backup', 'discarded', 'exported'))
           or (ns.manual_status = 'pending' and ns.status = 'ready_for_export')
           or (ns.manual_summary is not null)
        order by ns.article_id, ns.updated_at desc nulls last, ns.created_at desc nulls last
        on conflict (article_id) do nothing;
    else
        insert into public.manual_reviews (
            article_id,
            status,
            summary,
            rank,
            notes,
            score,
            decided_by,
            decided_at,
            created_at,
            updated_at
        )
        select distinct on (ns.article_id)
            ns.article_id,
            case when ns.manual_status = 'approved' then 'selected' else ns.manual_status end as status,
            ns.manual_summary,
            null::double precision as rank,
            ns.manual_notes,
            ns.manual_score,
            ns.manual_decided_by,
            ns.manual_decided_at,
            coalesce(ns.created_at, now()),
            coalesce(ns.updated_at, now())
        from public.news_summaries ns
        where (ns.manual_status in ('selected', 'backup', 'discarded', 'exported'))
           or (ns.manual_status = 'pending' and ns.status = 'ready_for_export')
           or (ns.manual_summary is not null)
        order by ns.article_id, ns.updated_at desc nulls last, ns.created_at desc nulls last
        on conflict (article_id) do nothing;
    end if;
end $$;

commit;
