begin;

alter table if exists public.manual_reviews
    add column if not exists report_type text check (report_type in ('zongbao', 'wanbao'));

update public.manual_reviews
set report_type = 'zongbao'
where report_type is null;

drop index if exists manual_reviews_pending_idx;
drop index if exists manual_reviews_status_idx;

create index if not exists manual_reviews_pending_idx
    on public.manual_reviews (coalesce(report_type, 'zongbao'), rank asc nulls last, article_id)
    where status = 'pending';

create index if not exists manual_reviews_status_idx
    on public.manual_reviews (status, coalesce(report_type, 'zongbao'));

create index if not exists manual_reviews_status_report_type_rank_idx
    on public.manual_reviews (status, coalesce(report_type, 'zongbao'), rank asc nulls last, article_id);

commit;
