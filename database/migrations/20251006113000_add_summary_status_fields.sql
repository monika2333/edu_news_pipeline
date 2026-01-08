-- migrate:up
-- Two-stage summary status columns
ALTER TABLE IF EXISTS public.news_summaries
    ADD COLUMN IF NOT EXISTS summary_status text NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS summary_attempted_at timestamptz,
    ADD COLUMN IF NOT EXISTS summary_fail_count integer NOT NULL DEFAULT 0;

-- Backfill legacy rows: completed when llm_summary already populated
UPDATE public.news_summaries
SET summary_status = CASE
        WHEN COALESCE(TRIM(llm_summary), '') <> '' THEN 'completed'
        ELSE summary_status
    END;

CREATE INDEX IF NOT EXISTS news_summaries_status_attempt_idx
    ON public.news_summaries (summary_status, summary_attempted_at);

-- migrate:down
