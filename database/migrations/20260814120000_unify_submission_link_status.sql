-- migrate:up
alter table public.submitted_report_items
    drop constraint submitted_report_items_link_status_check;

update public.submitted_report_items
set link_status = 'matched'
where link_status in ('exact', 'fuzzy', 'manual');

alter table public.submitted_report_items
    add constraint submitted_report_items_link_status_check check (
        link_status in (
            'processing',
            'pending',
            'matched',
            'unmatched',
            'rejected'
        )
    );

-- migrate:down
alter table public.submitted_report_items
    drop constraint submitted_report_items_link_status_check;

-- The original matching provenance cannot be reconstructed after unification.
update public.submitted_report_items
set link_status = 'manual'
where link_status = 'matched';

alter table public.submitted_report_items
    add constraint submitted_report_items_link_status_check check (
        link_status in (
            'processing',
            'pending',
            'exact',
            'fuzzy',
            'manual',
            'unmatched',
            'rejected'
        )
    );
