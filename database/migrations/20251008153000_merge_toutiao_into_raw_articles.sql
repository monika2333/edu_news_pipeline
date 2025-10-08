-- Merge existing Toutiao rows into raw_articles without duplication
-- Safe to run multiple times.

begin;

do $$
begin
    if to_regclass('public.toutiao_articles') is not null
       and to_regclass('public.raw_articles') is not null then
        execute $$
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
        $$;
    end if;
end$$;

commit;

