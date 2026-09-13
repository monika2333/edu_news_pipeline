-- migrate:up
-- Add llm_source column to track which model generated the summary
do $migration$
begin
    if to_regclass('public.news_summaries') is not null then
        execute 'alter table public.news_summaries add column if not exists llm_source text';
        execute 'comment on column public.news_summaries.llm_source is ''Identifier for the LLM model used to generate the summary.''';
    end if;
end
$migration$;

-- migrate:down
