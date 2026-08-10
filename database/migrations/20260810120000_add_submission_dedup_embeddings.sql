-- migrate:up

alter table if exists public.news_summaries
    add column if not exists dedup_embedding bytea,
    add column if not exists dedup_embedding_model text,
    add column if not exists dedup_source_hash text,
    add column if not exists dedup_embedded_at timestamptz;

comment on column public.news_summaries.dedup_embedding is
    'Normalized title-plus-summary embedding used by submission-dedup.';
comment on column public.news_summaries.dedup_embedding_model is
    'Embedding model used for dedup_embedding; must match the submission-dedup model constant.';
comment on column public.news_summaries.dedup_source_hash is
    'SHA-256 of the exact title-plus-truncated-summary text encoded for submission-dedup.';
comment on column public.news_summaries.dedup_embedded_at is
    'Timestamp when submission-dedup last refreshed the cached news embedding.';

-- migrate:down

alter table if exists public.news_summaries
    drop column if exists dedup_embedded_at,
    drop column if exists dedup_source_hash,
    drop column if exists dedup_embedding_model,
    drop column if exists dedup_embedding;
