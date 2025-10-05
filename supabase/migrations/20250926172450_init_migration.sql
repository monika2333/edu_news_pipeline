-- Supabase schema for Edu News Automation System
-- Run with `supabase db push` after copying into supabase/config

create table if not exists public.sources (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    type text check (type in ('official_media', 'aggregator', 'social', 'other')) default 'other',
    base_url text,
    priority smallint default 0,
    is_active boolean not null default true,
    metadata jsonb default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.raw_articles (
    id uuid primary key default gen_random_uuid(),
    source_id uuid references public.sources(id) on delete set null,
    title text not null,
    content text,
    author text,
    published_at timestamptz,
    url text,
    raw_payload jsonb,
    hash text not null,
    language text default 'zh',
    status text check (status in ('fetched','failed','ignored')) default 'fetched',
    is_deleted boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (hash)
);

create index if not exists raw_articles_source_id_idx on public.raw_articles(source_id);
create index if not exists raw_articles_published_at_idx on public.raw_articles(published_at);

create table if not exists public.filtered_articles (
    id uuid primary key default gen_random_uuid(),
    raw_article_id uuid not null references public.raw_articles(id) on delete cascade,
    relevance_score numeric(5,2) default 0,
    status text check (status in ('pending','approved','rejected')) default 'pending',
    keywords text[] default '{}',
    dedup_group_id uuid,
    processed_payload jsonb,
    summary text,
    importance_score numeric(5,2) default 0,
    is_deleted boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists filtered_articles_raw_article_id_idx on public.filtered_articles(raw_article_id);
create index if not exists filtered_articles_dedup_group_idx on public.filtered_articles(dedup_group_id);
create index if not exists filtered_articles_keywords_idx on public.filtered_articles using gin(keywords);


create table if not exists public.brief_batches (
    id uuid primary key default gen_random_uuid(),
    report_date date not null,
    sequence_no integer default 1,
    generated_at timestamptz not null default now(),
    generated_by text,
    export_payload jsonb,
    unique (report_date, sequence_no)
);

create table if not exists public.brief_items (
    id uuid primary key default gen_random_uuid(),
    brief_batch_id uuid not null references public.brief_batches(id) on delete cascade,
    section text check (section in ('primary_school','high_school','higher_education','other')) default 'other',
    order_index integer not null default 0,
    final_summary text,
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists brief_items_batch_idx on public.brief_items(brief_batch_id);
create index if not exists brief_items_section_idx on public.brief_items(section);


-- Helper view to inspect latest brief summaries
create or replace view public.latest_brief_items as
select
    bb.report_date,
    bi.section,
    bi.order_index,
    bi.final_summary,
    bi.approved_by,
    bi.approved_at
from public.brief_items bi
join public.brief_batches bb on bb.id = bi.brief_batch_id
where (bb.report_date, bi.section, bi.order_index) in (
    select bb.report_date, bi.section, max(bi.order_index)
    from public.brief_items bi
    join public.brief_batches bb on bb.id = bi.brief_batch_id
    group by bb.report_date, bi.section
);

-- Trigger to keep updated_at in sync
create or replace function public.set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create trigger set_updated_at_sources
before update on public.sources
for each row execute function public.set_updated_at();

create trigger set_updated_at_raw_articles
before update on public.raw_articles
for each row execute function public.set_updated_at();

create trigger set_updated_at_filtered_articles
before update on public.filtered_articles
for each row execute function public.set_updated_at();

create trigger set_updated_at_brief_items
before update on public.brief_items
for each row execute function public.set_updated_at();
