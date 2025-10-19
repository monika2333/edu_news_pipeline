-- Introduce filtered_articles and primary_articles tables, plus extend news_summaries
-- Run with: psql -f database/migrations/20251018120000_add_filtered_and_primary_articles.sql

begin;

create table if not exists public.filtered_articles (
    article_id text primary key,
    keywords text[] not null default '{}'::text[],
    status text not null default 'pending',
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamptz,
    url text,
    content_markdown text,
    content_hash text,
    simhash text,
    primary_article_id text,
    inserted_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint filtered_articles_raw_fk
        foreign key (article_id)
        references public.raw_articles(article_id)
        on delete cascade
);

alter table public.filtered_articles
    add constraint filtered_articles_primary_fk
        foreign key (primary_article_id)
        references public.filtered_articles(article_id)
        on delete set null
        deferrable initially deferred;

create unique index if not exists filtered_articles_content_hash_uidx
    on public.filtered_articles (content_hash)
    where content_hash is not null;

create index if not exists filtered_articles_status_idx
    on public.filtered_articles (status);

create index if not exists filtered_articles_primary_idx
    on public.filtered_articles (primary_article_id);

create index if not exists filtered_articles_simhash_idx
    on public.filtered_articles (simhash)
    where simhash is not null;

drop trigger if exists filtered_articles_set_updated_at on public.filtered_articles;
create trigger filtered_articles_set_updated_at
    before update on public.filtered_articles
    for each row execute function public.set_updated_at();

create table if not exists public.primary_articles (
    article_id text primary key,
    primary_article_id text not null,
    status text not null default 'pending',
    score numeric(6,3),
    score_updated_at timestamptz,
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamptz,
    url text,
    content_markdown text,
    keywords text[] not null default '{}'::text[],
    content_hash text,
    simhash text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint primary_articles_filtered_fk
        foreign key (article_id)
        references public.filtered_articles(article_id)
        on delete cascade,
    constraint primary_articles_primary_fk
        foreign key (primary_article_id)
        references public.filtered_articles(article_id)
        on delete restrict
        deferrable initially deferred
);

create index if not exists primary_articles_status_idx
    on public.primary_articles (status);

create index if not exists primary_articles_primary_idx
    on public.primary_articles (primary_article_id);

create index if not exists primary_articles_score_idx
    on public.primary_articles (score desc nulls last);

drop trigger if exists primary_articles_set_updated_at on public.primary_articles;
create trigger primary_articles_set_updated_at
    before update on public.primary_articles
    for each row execute function public.set_updated_at();

alter table public.news_summaries
    add column if not exists score numeric(6,3),
    add column if not exists status text not null default 'pending',
    add column if not exists sentiment_label text,
    add column if not exists sentiment_confidence double precision;

create index if not exists news_summaries_status_idx
    on public.news_summaries (status);

create index if not exists news_summaries_sentiment_idx
    on public.news_summaries (sentiment_label);

create index if not exists news_summaries_score_idx
    on public.news_summaries (score desc nulls last);

commit;
