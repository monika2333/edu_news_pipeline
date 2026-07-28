-- migrate:up
create table if not exists public.submitted_reports (
    id uuid primary key default gen_random_uuid(),
    report_type text not null,
    report_date date not null,
    compiled_date date not null,
    issue_no text,
    title_line text,
    pasted_text text not null,
    item_count integer not null default 0,
    imported_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submitted_reports_type_check check (
        report_type in ('zongbao', 'wanbao', 'feedback')
    )
);

create index if not exists submitted_reports_type_date_idx
    on public.submitted_reports (report_type, report_date desc);
create index if not exists submitted_reports_report_date_idx
    on public.submitted_reports (report_date desc);

create table if not exists public.submitted_report_items (
    id uuid primary key default gen_random_uuid(),
    report_id uuid not null references public.submitted_reports(id) on delete cascade,
    section text,
    marker text,
    order_index integer not null default 0,
    title text not null,
    body text not null default '',
    source text,
    urls text[] not null default '{}'::text[],
    norm_title text not null,
    norm_title_hash text not null,
    embedding bytea,
    embedding_model text,
    embedded_at timestamptz,
    article_id text,
    link_status text not null default 'pending',
    link_title_score numeric(5,4),
    link_body_score numeric(5,4),
    link_combined_score numeric(5,4),
    best_candidate_article_id text,
    link_matched_at timestamptz,
    link_decided_by uuid references public.console_users(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submitted_report_items_link_status_check check (
        link_status in (
            'pending',
            'exact',
            'fuzzy',
            'manual',
            'unmatched',
            'rejected'
        )
    )
);

create index if not exists submitted_report_items_report_idx
    on public.submitted_report_items (report_id, order_index);
create index if not exists submitted_report_items_norm_hash_idx
    on public.submitted_report_items (norm_title_hash);
create index if not exists submitted_report_items_article_idx
    on public.submitted_report_items (article_id)
    where article_id is not null;
create index if not exists submitted_report_items_link_pending_idx
    on public.submitted_report_items (link_status)
    where link_status = 'pending';

create table if not exists public.submission_duplicate_matches (
    id uuid primary key default gen_random_uuid(),
    article_id text not null,
    item_id uuid not null references public.submitted_report_items(id) on delete cascade,
    similarity numeric(5,4) not null,
    match_method text not null,
    state text not null default 'suspected',
    decided_by uuid references public.console_users(id) on delete set null,
    decided_at timestamptz,
    detected_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint submission_duplicate_matches_method_check check (
        match_method in ('exact', 'vector', 'llm', 'manual')
    ),
    constraint submission_duplicate_matches_state_check check (
        state in ('suspected', 'confirmed', 'dismissed')
    ),
    constraint submission_duplicate_matches_unique unique (article_id, item_id)
);

create index if not exists submission_duplicate_matches_article_idx
    on public.submission_duplicate_matches (article_id);
create index if not exists submission_duplicate_matches_state_idx
    on public.submission_duplicate_matches (state);

-- migrate:down
drop table if exists public.submission_duplicate_matches;
drop table if exists public.submitted_report_items;
drop table if exists public.submitted_reports;
