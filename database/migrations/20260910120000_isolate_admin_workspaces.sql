-- migrate:up
create view public.active_console_admins as
select id
from public.console_users
where role = 'admin'
  and is_active
  and deleted_at is null;

alter table public.manual_reviews
    add column owner_user_id uuid;

do $$
begin
    if (
        exists (select 1 from public.manual_reviews)
        or exists (
            select 1
            from public.shift_reviews
            where admin_discarded_at is not null
        )
    ) and not exists (select 1 from public.active_console_admins) then
        raise exception
            'cannot isolate administrator workspaces without an active administrator';
    end if;
end
$$;

create temporary table legacy_manual_reviews
on commit drop
as select * from public.manual_reviews;

alter table public.manual_reviews
    drop constraint manual_reviews_article_id_key;

drop index public.manual_reviews_pending_idx;
drop index public.manual_reviews_status_idx;
drop index public.manual_reviews_status_report_type_rank_idx;

delete from public.manual_reviews;

insert into public.manual_reviews (
    id,
    owner_user_id,
    article_id,
    status,
    summary,
    rank,
    notes,
    score,
    decided_by,
    decided_at,
    created_at,
    updated_at,
    manual_llm_source,
    report_type,
    decided_by_user_id,
    version
)
select
    gen_random_uuid(),
    admin.id,
    legacy.article_id,
    legacy.status,
    legacy.summary,
    legacy.rank,
    legacy.notes,
    legacy.score,
    legacy.decided_by,
    legacy.decided_at,
    legacy.created_at,
    legacy.updated_at,
    legacy.manual_llm_source,
    legacy.report_type,
    legacy.decided_by_user_id,
    legacy.version
from legacy_manual_reviews legacy
cross join public.active_console_admins admin;

alter table public.manual_reviews
    alter column owner_user_id set not null,
    add constraint manual_reviews_owner_user_id_fkey
        foreign key (owner_user_id)
        references public.console_users(id)
        on delete restrict,
    add constraint manual_reviews_owner_article_unique
        unique (owner_user_id, article_id);

create index manual_reviews_pending_idx
    on public.manual_reviews (
        owner_user_id,
        coalesce(report_type, 'zongbao'),
        rank,
        article_id
    )
    where status = 'pending';

create index manual_reviews_status_idx
    on public.manual_reviews (
        owner_user_id,
        status,
        coalesce(report_type, 'zongbao')
    );

create index manual_reviews_status_report_type_rank_idx
    on public.manual_reviews (
        owner_user_id,
        status,
        coalesce(report_type, 'zongbao'),
        rank,
        article_id
    );

create table public.shift_review_admin_discards (
    owner_user_id uuid not null
        references public.console_users(id) on delete restrict,
    shift_review_id uuid not null
        references public.shift_reviews(id) on delete cascade,
    discarded_at timestamptz not null default now(),
    discarded_by_user_id uuid
        references public.console_users(id) on delete restrict,
    primary key (owner_user_id, shift_review_id)
);

create index shift_review_admin_discards_shift_idx
    on public.shift_review_admin_discards (shift_review_id, owner_user_id);

insert into public.shift_review_admin_discards (
    owner_user_id,
    shift_review_id,
    discarded_at,
    discarded_by_user_id
)
select
    admin.id,
    review.id,
    review.admin_discarded_at,
    review.admin_discarded_by_user_id
from public.shift_reviews review
cross join public.active_console_admins admin
where review.admin_discarded_at is not null;

drop index public.shift_reviews_admin_discarded_idx;

alter table public.shift_reviews
    drop constraint shift_reviews_admin_discarded_by_user_id_fkey,
    drop column admin_discarded_at,
    drop column admin_discarded_by_user_id;

-- migrate:down
alter table public.shift_reviews
    add column admin_discarded_at timestamptz,
    add column admin_discarded_by_user_id uuid;

with latest as (
    select distinct on (shift_review_id)
        shift_review_id,
        discarded_at,
        discarded_by_user_id
    from public.shift_review_admin_discards
    order by shift_review_id, discarded_at desc, owner_user_id
)
update public.shift_reviews review
set admin_discarded_at = latest.discarded_at,
    admin_discarded_by_user_id = latest.discarded_by_user_id
from latest
where review.id = latest.shift_review_id;

alter table public.shift_reviews
    add constraint shift_reviews_admin_discarded_by_user_id_fkey
        foreign key (admin_discarded_by_user_id)
        references public.console_users(id)
        on delete restrict;

create index shift_reviews_admin_discarded_idx
    on public.shift_reviews (shift_id, admin_discarded_at desc)
    where admin_discarded_at is not null;

drop table public.shift_review_admin_discards;

drop index public.manual_reviews_pending_idx;
drop index public.manual_reviews_status_idx;
drop index public.manual_reviews_status_report_type_rank_idx;

with ranked as (
    select
        id,
        row_number() over (
            partition by article_id
            order by updated_at desc, id
        ) as row_number
    from public.manual_reviews
)
delete from public.manual_reviews review
using ranked
where review.id = ranked.id
  and ranked.row_number > 1;

alter table public.manual_reviews
    drop constraint manual_reviews_owner_article_unique,
    drop constraint manual_reviews_owner_user_id_fkey,
    drop column owner_user_id,
    add constraint manual_reviews_article_id_key unique (article_id);

create index manual_reviews_pending_idx
    on public.manual_reviews (
        coalesce(report_type, 'zongbao'),
        rank,
        article_id
    )
    where status = 'pending';

create index manual_reviews_status_idx
    on public.manual_reviews (status, coalesce(report_type, 'zongbao'));

create index manual_reviews_status_report_type_rank_idx
    on public.manual_reviews (
        status,
        coalesce(report_type, 'zongbao'),
        rank,
        article_id
    );

drop view public.active_console_admins;
