-- migrate:up
-- Add manual_llm_source to manual_reviews for manual override of detected source
do $migration$
begin
    if to_regclass('public.manual_reviews') is not null then
        execute 'alter table public.manual_reviews add column if not exists manual_llm_source text';
    end if;
end
$migration$;

-- migrate:down
