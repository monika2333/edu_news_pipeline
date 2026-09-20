-- migrate:up transaction:false
-- 账号名称解析用 raw_articles.token 反查最近一条非空来源名，爬虫首判每轮
-- 读 DISTINCT token；token 无索引时两条路径都在数百万行上顺序扫描。
create index concurrently if not exists raw_articles_token_idx
    on public.raw_articles using btree (token);

-- migrate:down transaction:false
drop index concurrently if exists public.raw_articles_token_idx;
