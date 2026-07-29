-- migrate:up transaction:false
create index concurrently if not exists brief_items_article_created_at_idx
    on public.brief_items (article_id, created_at desc);

-- migrate:down transaction:false
drop index concurrently if exists public.brief_items_article_created_at_idx;
