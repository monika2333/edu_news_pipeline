-- migrate:up

alter table public.manual_clusters
    drop column if exists report_type;

-- migrate:down

alter table public.manual_clusters
    add column if not exists report_type text not null default 'zongbao';
