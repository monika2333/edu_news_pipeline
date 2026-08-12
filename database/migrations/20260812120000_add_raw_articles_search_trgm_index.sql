-- migrate:up transaction:false
create index concurrently if not exists raw_articles_search_expr_trgm
    on public.raw_articles
    using gin (
        ((coalesce(title, '') || ' ' || coalesce(content_markdown, '')))
        public.gin_trgm_ops
    );

-- migrate:down transaction:false
drop index concurrently if exists public.raw_articles_search_expr_trgm;
