-- migrate:up
-- Add manual_llm_source to manual_reviews for manual override of detected source
alter table if exists public.manual_reviews
    add column if not exists manual_llm_source text;

-- migrate:down
