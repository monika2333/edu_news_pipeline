-- migrate:up
alter table public.submitted_reports
    add column if not exists prior_match_completed_at timestamptz;

update public.submitted_reports
set prior_match_completed_at = now()
where prior_match_completed_at is null;

-- migrate:down
alter table public.submitted_reports
    drop column if exists prior_match_completed_at;
