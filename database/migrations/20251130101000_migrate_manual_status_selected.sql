-- migrate:up
begin;

update public.news_summaries
set manual_status = 'selected'
where manual_status = 'approved';

commit;

-- migrate:down
