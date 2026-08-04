-- migrate:up
drop table if exists public.manual_export_items;
drop table if exists public.manual_export_batches;

-- migrate:down
create table public.manual_export_batches (
    id uuid primary key default gen_random_uuid(),
    report_date date not null,
    sequence_no integer not null default 1,
    generated_at timestamptz not null default now(),
    generated_by text,
    export_payload jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (report_date, sequence_no)
);

create table public.manual_export_items (
    id uuid primary key default gen_random_uuid(),
    manual_export_batch_id uuid not null references public.manual_export_batches(id) on delete cascade,
    article_id text,
    section text,
    order_index integer not null default 0,
    final_summary text,
    approved_by text,
    approved_at timestamptz,
    metadata jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (manual_export_batch_id, article_id)
);

create index manual_export_items_batch_idx
    on public.manual_export_items (manual_export_batch_id);
create index manual_export_items_section_idx
    on public.manual_export_items (section);
create index manual_export_items_article_created_at_idx
    on public.manual_export_items (article_id, created_at desc);

create trigger manual_export_batches_set_updated_at
    before update on public.manual_export_batches
    for each row execute function public.set_updated_at();
create trigger manual_export_items_set_updated_at
    before update on public.manual_export_items
    for each row execute function public.set_updated_at();
