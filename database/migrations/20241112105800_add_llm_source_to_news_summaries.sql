-- Add llm_source column to track which model generated the summary
alter table if exists public.news_summaries
    add column if not exists llm_source text;

comment on column public.news_summaries.llm_source is 'Identifier for the LLM model used to generate the summary.';
