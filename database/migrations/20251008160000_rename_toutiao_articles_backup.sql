-- Rename toutiao_articles to toutiao_articles_backup for observation period
-- Safe to run multiple times.

begin;

do $$
begin
    if to_regclass('public.toutiao_articles') is not null
       and to_regclass('public.toutiao_articles_backup') is null then
        execute 'ALTER TABLE public.toutiao_articles RENAME TO toutiao_articles_backup';
    end if;
end$$;

commit;

