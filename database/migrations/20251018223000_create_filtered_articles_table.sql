-- Create filtered_articles table for keyword-scoped processing and ensure indexes/constraints align with the new pipeline

CREATE TABLE IF NOT EXISTS public.filtered_articles (
    article_id text PRIMARY KEY,
    primary_article_id text NOT NULL,
    keywords text[] NOT NULL DEFAULT '{}'::text[],
    title text,
    source text,
    publish_time bigint,
    publish_time_iso timestamptz,
    url text,
    content_markdown text,
    content_hash text,
    fingerprint text,
    sentiment_label text,
    sentiment_confidence double precision,
    inserted_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT filtered_articles_article_fk
        FOREIGN KEY (article_id) REFERENCES public.raw_articles(article_id)
            ON DELETE CASCADE
);

ALTER TABLE public.filtered_articles
    ADD CONSTRAINT filtered_articles_primary_fk
        FOREIGN KEY (primary_article_id)
        REFERENCES public.filtered_articles(article_id)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED;

CREATE UNIQUE INDEX IF NOT EXISTS filtered_articles_content_hash_uidx
    ON public.filtered_articles (content_hash)
    WHERE content_hash IS NOT NULL;

CREATE INDEX IF NOT EXISTS filtered_articles_primary_idx
    ON public.filtered_articles (primary_article_id);

CREATE INDEX IF NOT EXISTS filtered_articles_updated_idx
    ON public.filtered_articles (updated_at DESC);

DROP TRIGGER IF EXISTS filtered_articles_set_updated_at ON public.filtered_articles;
CREATE TRIGGER filtered_articles_set_updated_at
    BEFORE UPDATE ON public.filtered_articles
    FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();

