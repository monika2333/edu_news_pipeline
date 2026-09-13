-- migrate:up
alter table public.crawl_accounts
    add column display_name_synced_at timestamptz,
    add column display_name_error text;

-- migrate:down
alter table public.crawl_accounts
    drop column display_name_error,
    drop column display_name_synced_at;
