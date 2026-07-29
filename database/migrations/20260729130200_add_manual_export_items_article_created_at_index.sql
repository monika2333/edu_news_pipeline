-- migrate:up transaction:false
create index concurrently if not exists manual_export_items_article_created_at_idx
    on public.manual_export_items (article_id, created_at desc);

-- migrate:down transaction:false
drop index concurrently if exists public.manual_export_items_article_created_at_idx;
