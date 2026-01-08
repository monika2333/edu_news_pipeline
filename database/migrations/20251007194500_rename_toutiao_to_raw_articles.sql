-- migrate:up
-- Rename toutiao_articles to raw_articles and align indexes/triggers
-- Safe to run multiple times.

begin;

-- 1) Rename table if it exists and raw_articles not already present
do $$
begin
    if to_regclass('public.raw_articles') is null and to_regclass('public.toutiao_articles') is not null then
        execute 'ALTER TABLE public.toutiao_articles RENAME TO raw_articles';
    end if;
end$$;

-- 2) Rename indexes if present
do $$
begin
    if to_regclass('public.toutiao_articles_fetched_at_idx') is not null then
        execute 'ALTER INDEX public.toutiao_articles_fetched_at_idx RENAME TO raw_articles_fetched_at_idx';
    end if;
    if to_regclass('public.toutiao_articles_detail_fetched_idx') is not null then
        execute 'ALTER INDEX public.toutiao_articles_detail_fetched_idx RENAME TO raw_articles_detail_fetched_idx';
    end if;
end$$;

-- 3) Ensure updated_at trigger exists with new name
do $$
begin
    if to_regclass('public.raw_articles') is not null then
        -- If old trigger name exists on the (renamed) table, rename it
        if exists (
            select 1
            from pg_trigger t
            join pg_class c on c.oid = t.tgrelid
            join pg_namespace n on n.oid = c.relnamespace
            where t.tgname = 'toutiao_articles_set_updated_at'
              and n.nspname = 'public'
              and c.relname = 'raw_articles'
        ) then
            execute 'ALTER TRIGGER toutiao_articles_set_updated_at ON public.raw_articles RENAME TO raw_articles_set_updated_at';
        end if;

        -- If new trigger missing, create it
        if not exists (
            select 1
            from pg_trigger t
            join pg_class c on c.oid = t.tgrelid
            join pg_namespace n on n.oid = c.relnamespace
            where t.tgname = 'raw_articles_set_updated_at'
              and n.nspname = 'public'
              and c.relname = 'raw_articles'
        ) then
            execute 'CREATE TRIGGER raw_articles_set_updated_at
                     BEFORE UPDATE ON public.raw_articles
                     FOR EACH ROW EXECUTE FUNCTION public.set_updated_at()';
        end if;
    end if;
end$$;

commit;


-- migrate:down
