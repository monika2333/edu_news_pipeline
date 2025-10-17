-- Add sentiment and deduplication fields to raw_articles

CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE IF EXISTS public.raw_articles
    ADD COLUMN IF NOT EXISTS content_hash text,
    ADD COLUMN IF NOT EXISTS fingerprint text,
    ADD COLUMN IF NOT EXISTS primary_article_id text,
    ADD COLUMN IF NOT EXISTS sentiment_label text,
    ADD COLUMN IF NOT EXISTS sentiment_confidence numeric(5,4);

-- Backfill newly added columns where possible
UPDATE public.raw_articles
SET primary_article_id = article_id
WHERE primary_article_id IS NULL;

UPDATE public.raw_articles
SET content_hash = encode(digest(convert_to(content_markdown, 'UTF8'), 'sha256'), 'hex')
WHERE content_hash IS NULL
  AND content_markdown IS NOT NULL;

CREATE INDEX IF NOT EXISTS raw_articles_content_hash_idx
    ON public.raw_articles (content_hash);

CREATE INDEX IF NOT EXISTS raw_articles_primary_article_idx
    ON public.raw_articles (primary_article_id);
