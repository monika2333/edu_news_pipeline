from __future__ import annotations

import psycopg
from src.config import get_settings, load_environment


def main() -> None:
    load_environment()
    s = get_settings()
    conn = psycopg.connect(
        host=s.db_host,
        port=s.db_port,
        user=s.db_user,
        password=s.db_password,
        dbname=s.db_name,
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute("select to_regclass('public.raw_articles') is not null")
        exists = cur.fetchone()[0]
        print("raw_articles exists:", exists)
        if exists:
            cur.execute("select count(*) from public.raw_articles")
            print("raw_articles count:", cur.fetchone()[0])


if __name__ == "__main__":
    main()

