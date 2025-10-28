-- Add Beijing gate staging fields for two-step classification
-- Run with: psql -f database/migrations/20251105100000_add_beijing_gate_fields.sql

begin;

alter table public.news_summaries
    add column if not exists is_beijing_related_llm boolean,
    add column if not exists beijing_gate_checked_at timestamptz,
    add column if not exists beijing_gate_raw jsonb,
    add column if not exists beijing_gate_attempted_at timestamptz,
    add column if not exists beijing_gate_fail_count integer not null default 0;

create index if not exists news_summaries_beijing_gate_idx
    on public.news_summaries (beijing_gate_attempted_at, summary_generated_at)
    where status = 'pending_beijing_gate' and summary_status = 'completed';

comment on column public.news_summaries.is_beijing_related_llm is 'LLM-based Beijing relevance decision; NULL when not evaluated';
comment on column public.news_summaries.beijing_gate_checked_at is 'Timestamp when the LLM Beijing gate returned a definitive result';

commit;
