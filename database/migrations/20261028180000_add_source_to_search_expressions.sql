-- migrate:up transaction:false
-- 来源（source）纳入检索范围后，旧索引表达式不再被任何查询使用，因此就地换成
-- 含 source 的新表达式索引。两个索引都复用原来的名字：
-- ① baseline 迁移 20241101000000 里有 news_summaries_search_expr_trgm 的
--    create index if not exists，改名会让 test_migration_replay 的 baseline
--    回填用例在重放 baseline 后多出一个旧表达式索引；
-- ② drop + create 在同一事务内提交，其他会话在提交前仍看到旧索引，不存在
--    「索引已删、新索引还没建好」的空窗。
-- 这里不能使用 CREATE/DROP INDEX CONCURRENTLY：dbmate 与
-- tests/test_migration_replay.py 都把整段 up 当作一条语句串执行，PostgreSQL
-- 会把它包进隐式事务，CONCURRENTLY 会被拒绝。
drop index if exists public.raw_articles_search_expr_trgm;
create index if not exists raw_articles_search_expr_trgm
    on public.raw_articles
    using gin (
        ((coalesce(title, '') || ' ' || coalesce(content_markdown, '') || ' '
            || coalesce(source, '')))
        public.gin_trgm_ops
    );

drop index if exists public.news_summaries_search_expr_trgm;
create index if not exists news_summaries_search_expr_trgm
    on public.news_summaries
    using gin (
        ((coalesce(title, '') || ' ' || coalesce(llm_summary, '') || ' '
            || coalesce(content_markdown, '') || ' ' || coalesce(source, '')))
        public.gin_trgm_ops
    );

-- migrate:down transaction:false
drop index if exists public.news_summaries_search_expr_trgm;
create index if not exists news_summaries_search_expr_trgm
    on public.news_summaries
    using gin (
        ((coalesce(title, '') || ' ' || coalesce(llm_summary, '') || ' '
            || coalesce(content_markdown, '')))
        public.gin_trgm_ops
    );

drop index if exists public.raw_articles_search_expr_trgm;
create index if not exists raw_articles_search_expr_trgm
    on public.raw_articles
    using gin (
        ((coalesce(title, '') || ' ' || coalesce(content_markdown, '')))
        public.gin_trgm_ops
    );
