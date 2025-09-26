-- Supabase schema for 教工委新闻自动化简报系统
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

create table if not exists public.events (
    id uuid primary key default gen_random_uuid(),
    title text not null,
    summary text,
    importance_score numeric(5,2) default 0,
    source_level text check (source_level in ('national','provincial','municipal','campus','other')) default 'other',
    primary_source_url text,
    status text check (status in ('draft','active','archived')) default 'draft',
    tags text[] default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists events_status_idx on public.events(status);
create index if not exists events_tags_idx on public.events using gin(tags);

create table if not exists public.event_articles (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references public.events(id) on delete cascade,
    filtered_article_id uuid not null references public.filtered_articles(id) on delete cascade,
    role text check (role in ('primary','supplementary','background')) default 'supplementary',
    notes text,
    created_at timestamptz not null default now(),
    unique (event_id, filtered_article_id)
);

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
    event_id uuid references public.events(id) on delete set null,
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

create table if not exists public.audit_logs (
    id bigint primary key generated always as identity,
    entity_type text not null,
    entity_id uuid,
    action text not null,
    payload jsonb,
    created_by text,
    created_at timestamptz not null default now()
);

create index if not exists audit_logs_entity_idx on public.audit_logs(entity_type, entity_id);

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

create trigger set_updated_at_events
before update on public.events
for each row execute function public.set_updated_at();

create trigger set_updated_at_brief_items
before update on public.brief_items
for each row execute function public.set_updated_at();
