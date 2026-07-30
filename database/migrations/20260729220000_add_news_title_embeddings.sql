-- migrate:up

create table if not exists public.news_title_embeddings (
    article_id text primary key
        references public.news_summaries(article_id) on delete cascade,
    embedding bytea not null,
    model text not null,
    title_hash text not null,
    updated_at timestamptz not null default now()
);

-- migrate:down

drop table if exists public.news_title_embeddings;
