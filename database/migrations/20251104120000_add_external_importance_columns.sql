-- Add external importance fields for news_summaries external filter
-- Run with: psql -f database/migrations/20251104120000_add_external_importance_columns.sql

begin;

alter table public.news_summaries
    add column if not exists external_importance_status text not null default 'pending',
    add column if not exists external_importance_score numeric(6,3),
    add column if not exists external_importance_checked_at timestamptz,
    add column if not exists external_importance_raw jsonb;

create index if not exists news_summaries_external_filter_idx
    on public.news_summaries (is_beijing_related, sentiment_label, external_importance_status);

commit;
