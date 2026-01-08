-- migrate:up
-- Migrate correlation -> score and remove deprecated column/index
BEGIN;

-- Backfill score from correlation for existing data
UPDATE public.news_summaries
SET score = correlation,
    updated_at = NOW()
WHERE score IS NULL
  AND correlation IS NOT NULL;

-- Drop old index and column
DROP INDEX IF EXISTS news_summaries_correlation_idx;
ALTER TABLE public.news_summaries
    DROP COLUMN IF EXISTS correlation;

COMMIT;


-- migrate:down
