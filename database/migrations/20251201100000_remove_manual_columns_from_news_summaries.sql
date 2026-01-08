-- migrate:up
begin;

drop index if exists public.news_summaries_manual_status_idx;

alter table if exists public.news_summaries
    drop column if exists manual_status,
    drop column if exists manual_summary,
    drop column if exists manual_score,
    drop column if exists manual_notes,
    drop column if exists manual_decided_by,
    drop column if exists manual_decided_at,
    drop column if exists manual_rank;

commit;

-- migrate:down
