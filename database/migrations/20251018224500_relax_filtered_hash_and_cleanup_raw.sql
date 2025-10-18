-- Adjust filtered_articles hash constraint and clear legacy raw hash values

DROP INDEX IF EXISTS filtered_articles_content_hash_uidx;

CREATE INDEX IF NOT EXISTS filtered_articles_content_hash_idx
    ON public.filtered_articles (content_hash)
    WHERE content_hash IS NOT NULL;

UPDATE public.raw_articles
SET content_hash = NULL,
    fingerprint = NULL;

DROP INDEX IF EXISTS raw_articles_content_hash_idx;
