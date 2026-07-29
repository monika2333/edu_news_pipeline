-- migrate:up transaction:false
create index concurrently if not exists news_summaries_created_at_idx
    on public.news_summaries (created_at);

-- migrate:down transaction:false
drop index concurrently if exists public.news_summaries_created_at_idx;
