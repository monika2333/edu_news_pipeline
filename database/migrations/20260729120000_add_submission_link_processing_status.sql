-- migrate:up
alter table public.submitted_report_items
    drop constraint submitted_report_items_link_status_check;

alter table public.submitted_report_items
    alter column link_status set default 'processing';

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

-- migrate:down
alter table public.submitted_report_items
    drop constraint submitted_report_items_link_status_check;

update public.submitted_report_items
set link_status = 'pending'
where link_status = 'processing';

alter table public.submitted_report_items
    alter column link_status set default 'pending';

alter table public.submitted_report_items
    add constraint submitted_report_items_link_status_check check (
        link_status in (
            'pending',
            'exact',
            'fuzzy',
            'manual',
            'unmatched',
            'rejected'
        )
    );
