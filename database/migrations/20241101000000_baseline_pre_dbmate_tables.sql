-- migrate:up
-- Historical compatibility baseline for pre-Dbmate objects and legacy shapes
-- required by the backdated migrations. Existing databases remain unchanged.

create extension if not exists pg_trgm with schema public;
create extension if not exists pgcrypto with schema public;

create table if not exists public.brief_batches (
    id uuid primary key default gen_random_uuid(),
    report_date date not null,
    sequence_no integer not null default 1,
    generated_at timestamptz not null default now(),
    generated_by text,
    export_payload jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (report_date, sequence_no)
);

create table if not exists public.brief_items (
    id uuid primary key default gen_random_uuid(),
    brief_batch_id uuid not null references public.brief_batches(id) on delete cascade,
    article_id text,
    section text,
    order_index integer not null default 0,
    final_summary text,
    approved_by text,
    approved_at timestamptz,
    metadata jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

do $baseline$
begin
    if to_regclass('public.raw_articles') is null
       and to_regclass('public.toutiao_articles') is null then
        execute $table$
            create table if not exists public.toutiao_articles (
                token text,
                profile_url text,
                article_id text,
                title text,
                source text,
                publish_time bigint,
                publish_time_iso timestamptz,
                url text,
                summary text,
                comment_count integer,
                digg_count integer,
                content_markdown text,
                fetched_at timestamptz not null default now(),
                created_at timestamptz not null default now(),
                updated_at timestamptz not null default now(),
                constraint raw_articles_pkey primary key (article_id)
            )
        $table$;
    end if;
    if to_regclass('public.raw_articles') is null
       and to_regclass('public.toutiao_articles') is not null then
        execute 'create index if not exists toutiao_articles_fetched_at_idx on public.toutiao_articles (fetched_at desc)';
    end if;
end
$baseline$;

create table if not exists public.news_summaries (
    article_id text primary key,
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamptz,
    url text,
    content_markdown text,
    llm_summary text,
    summary_generated_at timestamptz not null default now(),
    fetched_at timestamptz,
    llm_keywords text[] default '{}'::text[],
    correlation numeric(6,3),
    is_beijing_related boolean,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

do $baseline$
begin
    if exists (
        select 1
        from information_schema.columns
        where table_schema = 'public'
          and table_name = 'news_summaries'
          and column_name = 'correlation'
    ) then
        execute 'create index if not exists news_summaries_correlation_idx on public.news_summaries (correlation desc nulls last)';
    end if;
end
$baseline$;

create index if not exists news_summaries_summary_generated_idx
    on public.news_summaries (summary_generated_at);

create index if not exists news_summaries_search_expr_trgm
    on public.news_summaries
    using gin (
        ((coalesce(title, '') || ' ' || coalesce(llm_summary, '') || ' '
            || coalesce(content_markdown, '')))
        public.gin_trgm_ops
    );

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

-- migrate:down
-- Intentionally empty: this historical baseline must never drop live tables.
