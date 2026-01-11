-- migrate:up
-- Add manual cluster cache table for manual filter clustering.

create table if not exists public.manual_clusters (
    report_type text not null default 'zongbao',
    bucket_key text not null,
    cluster_id text not null,
    item_ids text[] not null,
    created_at timestamptz not null default now(),
    constraint manual_clusters_cluster_id_unique unique (cluster_id),
    constraint manual_clusters_bucket_key_check check (
        bucket_key in ('internal_positive', 'internal_negative', 'external_positive', 'external_negative')
    )
);

create index if not exists manual_clusters_bucket_key_idx
    on public.manual_clusters (bucket_key);

-- migrate:down
