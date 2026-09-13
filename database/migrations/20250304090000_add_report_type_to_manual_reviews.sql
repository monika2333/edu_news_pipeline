-- migrate:up
begin;

do $migration$
begin
    if to_regclass('public.manual_reviews') is not null then
        execute 'alter table public.manual_reviews add column if not exists report_type text check (report_type in (''zongbao'', ''wanbao''))';
        execute 'update public.manual_reviews set report_type = ''zongbao'' where report_type is null';
        execute 'drop index if exists public.manual_reviews_pending_idx';
        execute 'drop index if exists public.manual_reviews_status_idx';
        execute 'create index if not exists manual_reviews_pending_idx on public.manual_reviews (coalesce(report_type, ''zongbao''), rank asc nulls last, article_id) where status = ''pending''';
        execute 'create index if not exists manual_reviews_status_idx on public.manual_reviews (status, coalesce(report_type, ''zongbao''))';
        execute 'create index if not exists manual_reviews_status_report_type_rank_idx on public.manual_reviews (status, coalesce(report_type, ''zongbao''), rank asc nulls last, article_id)';
    end if;
end
$migration$;

commit;

-- migrate:down
