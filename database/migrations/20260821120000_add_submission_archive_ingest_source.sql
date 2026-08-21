-- migrate:up
alter table public.submitted_reports
    add column ingest_source text not null default 'console',
    add column source_message_id text,
    add column source_sender_id text;

create unique index submitted_reports_source_message_uidx
    on public.submitted_reports (ingest_source, source_message_id);

comment on column public.submitted_reports.ingest_source is
    'Archive entry channel, such as console or feishu.';
comment on column public.submitted_reports.source_message_id is
    'Provider message id used for durable idempotency.';
comment on column public.submitted_reports.source_sender_id is
    'Provider-scoped sender id retained for audit.';

-- migrate:down
drop index if exists public.submitted_reports_source_message_uidx;

alter table public.submitted_reports
    drop column if exists source_sender_id,
    drop column if exists source_message_id,
    drop column if exists ingest_source;
