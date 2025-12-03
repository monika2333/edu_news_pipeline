-- Create tables for manual export history, isolated from worker exports

create table if not exists public.manual_export_batches (
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

create table if not exists public.manual_export_items (
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

create index if not exists manual_export_items_batch_idx
    on public.manual_export_items (manual_export_batch_id);

create index if not exists manual_export_items_section_idx
    on public.manual_export_items (section);

drop trigger if exists manual_export_batches_set_updated_at on public.manual_export_batches;
create trigger manual_export_batches_set_updated_at
    before update on public.manual_export_batches
    for each row execute function public.set_updated_at();

drop trigger if exists manual_export_items_set_updated_at on public.manual_export_items;
create trigger manual_export_items_set_updated_at
    before update on public.manual_export_items
    for each row execute function public.set_updated_at();

