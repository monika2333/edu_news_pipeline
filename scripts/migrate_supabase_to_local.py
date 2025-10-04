#!/usr/bin/env python3
"""Copy essential Supabase tables into the local Postgres database."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json
from supabase import create_client
from postgrest.exceptions import APIError

ENV_CANDIDATES = [
    Path('.env.local'),
    Path('.env'),
    Path('config') / 'abstract.env',
]


TABLE_SPECS: Dict[str, List[str]] = {
    "toutiao_articles": [
        "token",
        "profile_url",
        "article_id",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "summary",
        "comment_count",
        "digg_count",
        "content_markdown",
        "fetched_at",
        "created_at",
        "updated_at",
    ],
    "news_summaries": [
        "article_id",
        "title",
        "source",
        "publish_time",
        "publish_time_iso",
        "url",
        "content_markdown",
        "llm_summary",
        "summary_generated_at",
        "fetched_at",
        "llm_keywords",
        "correlation",
        "created_at",
        "updated_at",
    ],
    "brief_batches": [
        "id",
        "report_date",
        "sequence_no",
        "generated_at",
        "generated_by",
        "export_payload",
        "created_at",
        "updated_at",
    ],
    "brief_items": [
        "id",
        "brief_batch_id",
        "article_id",
        "section",
        "order_index",
        "final_summary",
        "metadata",
        "created_at",
        "updated_at",
    ],
    "pipeline_runs": [
        "run_id",
        "status",
        "trigger_source",
        "plan",
        "started_at",
        "finished_at",
        "steps_completed",
        "artifacts",
        "error_summary",
        "created_at",
        "updated_at",
    ],
    "pipeline_run_steps": [
        "run_id",
        "order_index",
        "step_name",
        "status",
        "started_at",
        "finished_at",
        "duration_seconds",
        "error",
        "created_at",
    ],
}

JSON_COLUMNS: Dict[str, set] = {
    'brief_batches': {'export_payload'},
    'brief_items': {'metadata'},
    'pipeline_runs': {'plan', 'artifacts'},
}


def load_env_files() -> None:
    for candidate in ENV_CANDIDATES:
        if not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            os.environ.setdefault(key, value)


def connect_supabase_client():
    supabase_url = os.environ.get("SUPABASE_URL")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_role_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the environment.")
    return create_client(supabase_url, service_role_key)


def connect_local() -> psycopg.Connection:
    host = os.environ.get("DB_HOST", "localhost")
    port = int(os.environ.get("DB_PORT") or 5432)
    dbname = os.environ.get("DB_NAME", "postgres")
    user = os.environ.get("DB_USER", "postgres")
    password = os.environ.get("DB_PASSWORD", "")
    return psycopg.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=dbname,
        autocommit=False,
        row_factory=dict_row,
    )


def fetch_supabase_rows(client, table: str):
    chunk = 1000
    start = 0
    rows: List[dict] = []
    while True:
        try:
            response = client.table(table).select('*').range(start, start + chunk - 1).execute()
        except APIError as exc:
            if getattr(exc, 'code', None) == 'PGRST205':
                print(f"[warn] Supabase table '{table}' not found; skipping.")
                return []
            raise
        data = response.data or []
        rows.extend(data)
        if len(data) < chunk:
            break
        start += chunk
    return rows


def truncate_local(conn: psycopg.Connection, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(sql.SQL("TRUNCATE TABLE {} CASCADE").format(sql.Identifier("public", table)))


def insert_local(conn: psycopg.Connection, table: str, columns: List[str], rows: List[dict]) -> None:
    if not rows:
        return
    insert_columns = [col for col in columns if any(col in row for row in rows)]
    if not insert_columns:
        return
    json_cols = JSON_COLUMNS.get(table, set())
    column_idents = [sql.Identifier(col) for col in insert_columns]
    placeholders = sql.SQL(', ').join(sql.Placeholder() for _ in insert_columns)
    insert_sql = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
        sql.Identifier('public', table),
        sql.SQL(', ').join(column_idents),
        placeholders,
    )

    def _coerce(col: str, value):
        if value is None:
            return None
        if isinstance(value, dict) or col in json_cols:
            return Json(value)
        return value

    data = [tuple(_coerce(col, row.get(col)) for col in insert_columns) for row in rows]
    with conn.cursor() as cur:
        cur.executemany(insert_sql, data)


def migrate_table(client, local_conn: psycopg.Connection, table: str) -> int:
    desired = TABLE_SPECS[table]
    rows = fetch_supabase_rows(client, table)
    if rows:
        remote_columns = set().union(*(row.keys() for row in rows))
        columns = [col for col in desired if col in remote_columns]
    else:
        columns = []
    truncate_local(local_conn, table)
    insert_local(local_conn, table, columns, rows)
    return len(rows)


def main() -> None:
    load_env_files()
    client = connect_supabase_client()
    local_conn = connect_local()
    try:
        for table in TABLE_SPECS:
            count = migrate_table(client, local_conn, table)
            local_conn.commit()
            print(f"Migrated {count} rows from Supabase.{table} -> local.{table}")
    finally:
        local_conn.close()


if __name__ == "__main__":
    main()
