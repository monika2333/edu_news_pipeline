-- Supabase schema for Edu News Automation System
-- Run with `supabase db push` after copying into supabase/config

create table if not exists public.brief_batches (
    id uuid primary key default gen_random_uuid(),
    report_date date not null,
    sequence_no integer default 1,
    generated_at timestamptz not null default now(),
    generated_by text,
    export_payload jsonb,
    unique (report_date, sequence_no)
);

create table if not exists public.brief_items (
    id uuid primary key default gen_random_uuid(),
    brief_batch_id uuid not null references public.brief_batches(id) on delete cascade,
    section text check (section in ('primary_school','high_school','higher_education','other')) default 'other',
    order_index integer not null default 0,
    final_summary text,
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists brief_items_batch_idx on public.brief_items(brief_batch_id);
create index if not exists brief_items_section_idx on public.brief_items(section);


-- Helper view to inspect latest brief summaries
create or replace view public.latest_brief_items as
select
    bb.report_date,
    bi.section,
    bi.order_index,
    bi.final_summary,
    bi.approved_by,
    bi.approved_at
from public.brief_items bi
join public.brief_batches bb on bb.id = bi.brief_batch_id
where (bb.report_date, bi.section, bi.order_index) in (
    select bb.report_date, bi.section, max(bi.order_index)
    from public.brief_items bi
    join public.brief_batches bb on bb.id = bi.brief_batch_id
    group by bb.report_date, bi.section
);

-- Trigger to keep updated_at in sync
create or replace function public.set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;


create trigger set_updated_at_brief_items
before update on public.brief_items
for each row execute function public.set_updated_at();
