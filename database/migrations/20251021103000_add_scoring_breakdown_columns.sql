-- Add granular scoring columns to primary_articles and news_summaries
-- Run with: psql -f database/migrations/20251021103000_add_scoring_breakdown_columns.sql

begin;

alter table public.primary_articles
    add column if not exists raw_relevance_score numeric(6,3),
    add column if not exists keyword_bonus_score numeric(6,3),
    add column if not exists score_details jsonb not null default '{}'::jsonb;

alter table public.news_summaries
    add column if not exists raw_relevance_score numeric(6,3),
    add column if not exists keyword_bonus_score numeric(6,3),
    add column if not exists score_details jsonb not null default '{}'::jsonb;

update public.primary_articles
set
    raw_relevance_score = coalesce(raw_relevance_score, score),
    keyword_bonus_score = coalesce(keyword_bonus_score, 0),
    score_details = coalesce(score_details, '{}'::jsonb);

update public.news_summaries
set
    raw_relevance_score = coalesce(raw_relevance_score, score),
    keyword_bonus_score = coalesce(keyword_bonus_score, 0),
    score_details = coalesce(score_details, '{}'::jsonb);

commit;
