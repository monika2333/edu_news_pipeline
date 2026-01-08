-- migrate:up
-- Add detail_fetched_at column to track successful detail enrichment
alter table if exists public.toutiao_articles
    add column if not exists detail_fetched_at timestamptz;

create index if not exists toutiao_articles_detail_fetched_idx
    on public.toutiao_articles (detail_fetched_at desc);

-- migrate:down
