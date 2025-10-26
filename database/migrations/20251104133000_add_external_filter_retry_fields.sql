-- Add retry tracking for external filter worker
-- Run with: psql -f database/migrations/20251104133000_add_external_filter_retry_fields.sql

begin;

alter table public.news_summaries
    add column if not exists external_filter_attempted_at timestamptz,
    add column if not exists external_filter_fail_count integer not null default 0;

commit;
