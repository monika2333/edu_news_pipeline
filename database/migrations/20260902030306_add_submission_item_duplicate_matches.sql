-- migrate:up
create table if not exists public.submission_item_duplicate_matches (
    id uuid primary key default gen_random_uuid(),
    item_id uuid not null
        references public.submitted_report_items(id) on delete cascade,
    prior_item_id uuid not null
        references public.submitted_report_items(id) on delete cascade,
    similarity numeric(5,4) not null,
    match_method text not null,
    detected_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submission_item_duplicate_matches_method_check check (
        match_method in ('article', 'title_hash', 'vector')
    ),
    constraint submission_item_duplicate_matches_not_self check (
        item_id <> prior_item_id
    ),
    constraint submission_item_duplicate_matches_unique unique (
        item_id, prior_item_id
    )
);

create index if not exists submission_item_duplicate_matches_item_idx
    on public.submission_item_duplicate_matches (item_id);


-- migrate:down
drop table if exists public.submission_item_duplicate_matches;
