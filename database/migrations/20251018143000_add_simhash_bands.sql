-- migrate:up
-- Add SimHash bigint and band columns for near-duplicate detection
begin;

alter table public.filtered_articles
    add column if not exists simhash_bigint bigint,
    add column if not exists simhash_band1 integer,
    add column if not exists simhash_band2 integer,
    add column if not exists simhash_band3 integer,
    add column if not exists simhash_band4 integer;

create index if not exists filtered_articles_simhash_band1_idx
    on public.filtered_articles (simhash_band1);

create index if not exists filtered_articles_simhash_band2_idx
    on public.filtered_articles (simhash_band2);

create index if not exists filtered_articles_simhash_band3_idx
    on public.filtered_articles (simhash_band3);

create index if not exists filtered_articles_simhash_band4_idx
    on public.filtered_articles (simhash_band4);

commit;

-- migrate:down
