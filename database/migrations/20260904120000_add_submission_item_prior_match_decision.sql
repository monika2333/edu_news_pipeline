-- migrate:up
alter table public.submitted_report_items
    add column prior_match_decision text,
    add column prior_match_decided_by uuid
        references public.console_users(id) on delete set null,
    add column prior_match_decided_at timestamptz,
    add constraint submitted_report_items_prior_match_decision_check check (
        prior_match_decision is null
        or prior_match_decision in ('submitted', 'not_submitted')
    );

-- migrate:down
alter table public.submitted_report_items
    drop constraint submitted_report_items_prior_match_decision_check,
    drop column prior_match_decided_at,
    drop column prior_match_decided_by,
    drop column prior_match_decision;
