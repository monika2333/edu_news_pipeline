-- Adjust filtered_articles indexes to allow duplicate hashes
begin;

drop index if exists filtered_articles_content_hash_uidx;
create index if not exists filtered_articles_content_hash_idx
    on public.filtered_articles (content_hash)
    where content_hash is not null;

commit;
