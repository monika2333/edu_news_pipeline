-- migrate:up
alter table public.score_feedbacks
    add column if not exists submitted_by text;

alter table public.score_feedbacks
    add column if not exists submitted_by_user_id uuid
        references public.console_users(id) on delete restrict;

create index if not exists score_feedbacks_submitted_by_user_idx
    on public.score_feedbacks (submitted_by_user_id);

-- migrate:down
drop index if exists public.score_feedbacks_submitted_by_user_idx;

alter table public.score_feedbacks
    drop column if exists submitted_by_user_id;

alter table public.score_feedbacks
    drop column if exists submitted_by;
