-- migrate:up
-- Merge existing Toutiao rows into raw_articles without duplication
-- Safe to run multiple times.

begin;

do $$
begin
    if to_regclass('public.toutiao_articles') is not null
       and to_regclass('public.raw_articles') is not null then
        execute $merge$
            INSERT INTO public.raw_articles (
                token,
                profile_url,
                article_id,
                title,
                source,
                publish_time,
                publish_time_iso,
                url,
                summary,
                comment_count,
                digg_count,
                content_markdown,
                detail_fetched_at,
                fetched_at,
                created_at,
                updated_at
            )
            SELECT 
                token,
                profile_url,
                article_id,
                title,
                source,
                publish_time,
                publish_time_iso,
                url,
                summary,
                comment_count,
                digg_count,
                content_markdown,
                detail_fetched_at,
                fetched_at,
                created_at,
                updated_at
            FROM public.toutiao_articles
            ON CONFLICT (article_id) DO NOTHING
        $merge$;
    end if;
end$$;

-- Reconcile pre-Dbmate updated_at triggers with the live schema snapshot.
do $triggers$
begin
    if to_regclass('public.brief_items') is not null
       and exists (
           select 1
           from pg_catalog.pg_trigger
           where tgrelid = to_regclass('public.brief_items')
             and tgname = 'set_updated_at_brief_items'
             and not tgisinternal
       ) then
        execute 'drop trigger set_updated_at_brief_items on public.brief_items';
    end if;

    if to_regclass('public.pipeline_runs') is not null
       and not exists (
           select 1
           from pg_catalog.pg_trigger
           where tgrelid = to_regclass('public.pipeline_runs')
             and tgname = 'pipeline_runs_set_updated_at'
             and not tgisinternal
       ) then
        execute 'create trigger pipeline_runs_set_updated_at before update on public.pipeline_runs for each row execute function public.set_updated_at()';
    end if;
end
$triggers$;

commit;


-- migrate:down
