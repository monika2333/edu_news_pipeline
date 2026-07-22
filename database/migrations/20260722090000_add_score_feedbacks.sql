-- migrate:up
create extension if not exists pgcrypto;

create table if not exists public.score_feedbacks (
    id uuid primary key default gen_random_uuid(),
    article_id text not null references public.news_summaries(article_id) on delete cascade,
    feedback_type text not null,
    score_value numeric(6,3) not null,
    prompt_key text not null,
    prompt_version text not null,
    notes text,
    score_context jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint score_feedbacks_feedback_type_check check (
        feedback_type in ('too_high', 'too_low')
    ),
    constraint score_feedbacks_prompt_key_check check (
        prompt_key in ('external_positive', 'external_negative', 'internal_positive', 'internal_negative')
    ),
    constraint score_feedbacks_prompt_version_check check (
        btrim(prompt_version) <> ''
    ),
    constraint score_feedbacks_notes_length_check check (
        notes is null or char_length(notes) <= 500
    ),
    constraint score_feedbacks_article_prompt_unique unique (
        article_id,
        prompt_key,
        prompt_version
    )
);

create index if not exists score_feedbacks_prompt_version_idx
    on public.score_feedbacks (prompt_key, prompt_version);

-- migrate:down
drop table if exists public.score_feedbacks;
