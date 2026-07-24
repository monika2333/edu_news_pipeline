-- migrate:up
alter table public.console_users
    add column if not exists preferred_weekday smallint;

alter table public.console_users
    drop constraint if exists console_users_preferred_weekday_check;

alter table public.console_users
    add constraint console_users_preferred_weekday_check
    check (preferred_weekday is null or preferred_weekday between 0 and 6);

update public.console_users as u
set preferred_weekday = s.weekday
from public.duty_schedules as s
where s.user_id = u.id
  and u.preferred_weekday is null;

-- migrate:down
alter table public.console_users
    drop constraint if exists console_users_preferred_weekday_check;

alter table public.console_users
    drop column if exists preferred_weekday;
