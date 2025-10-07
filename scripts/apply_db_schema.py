from __future__ import annotations

import os
from pathlib import Path

import psycopg

from src.config import get_settings, load_environment


def apply_sql_file(conn: psycopg.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)


def main() -> None:
    load_environment()
    settings = get_settings()
    conn = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=True,
    )
    repo = Path(__file__).resolve().parents[1]
    schema = repo / "database" / "schema.sql"
    apply_sql_file(conn, schema)

    # Apply rename migration (idempotent)
    mig = repo / "database" / "migrations" / "20251007194500_rename_toutiao_to_raw_articles.sql"
    if mig.exists():
        apply_sql_file(conn, mig)
    print("Schema and migrations applied.")


if __name__ == "__main__":
    main()

