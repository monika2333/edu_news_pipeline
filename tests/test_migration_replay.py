from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote
from uuid import uuid4

import psycopg
from psycopg import sql

from src.config import get_settings


ROOT = Path(__file__).parents[1]
MIGRATIONS_DIR = ROOT / "database" / "migrations"
SCHEMA_PATH = ROOT / "database" / "schema.sql"
BASELINE_VERSION = "20241101000000"
BASELINE_FILENAME = f"{BASELINE_VERSION}_baseline_pre_dbmate_tables.sql"
BACKDATED_MIGRATIONS = (
    "20241112105800_add_llm_source_to_news_summaries.sql",
    "20250219090000_add_manual_llm_source_to_manual_reviews.sql",
    "20250304090000_add_report_type_to_manual_reviews.sql",
)


@contextmanager
def _isolated_databases(count: int) -> Iterator[list[str]]:
    settings = get_settings()
    names = [f"test_migration_replay_{uuid4().hex}" for _ in range(count)]
    maintenance = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=True,
    )
    try:
        for name in names:
            maintenance.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))
            )
        yield names
    finally:
        for name in names:
            maintenance.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )
        maintenance.close()


def _connect(database_name: str) -> psycopg.Connection:
    settings = get_settings()
    return psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=database_name,
        autocommit=True,
    )


def _database_url(database_name: str) -> str:
    settings = get_settings()
    user = quote(settings.db_user, safe="")
    password = ""
    if settings.db_password is not None:
        password = f":{quote(settings.db_password, safe='')}"
    host = quote(settings.db_host, safe="[]:")
    database = quote(database_name, safe="")
    return (
        f"postgresql://{user}{password}@{host}:{settings.db_port}/{database}"
        "?sslmode=disable"
    )


def _run_dbmate(database_name: str, command: str) -> str:
    executable = shutil.which("dbmate")
    assert executable is not None, "dbmate must be installed to verify migration history"
    env = {**os.environ, "DATABASE_URL": _database_url(database_name)}
    result = subprocess.run(
        [
            executable,
            "--migrations-dir",
            str(MIGRATIONS_DIR),
            "--schema-file",
            str(SCHEMA_PATH),
            "--no-dump-schema",
            command,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def _load_schema(database_name: str) -> None:
    executable = shutil.which("psql")
    assert executable is not None, "psql must be installed to load database/schema.sql"
    settings = get_settings()
    env = dict(os.environ)
    if settings.db_password is None:
        env.pop("PGPASSWORD", None)
    else:
        env["PGPASSWORD"] = settings.db_password
    result = subprocess.run(
        [
            executable,
            "--host",
            settings.db_host,
            "--port",
            str(settings.db_port),
            "--username",
            settings.db_user,
            "--dbname",
            database_name,
            "--set",
            "ON_ERROR_STOP=1",
            "--file",
            str(SCHEMA_PATH),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _migration_up(filename: str) -> str:
    source = (MIGRATIONS_DIR / filename).read_text(encoding="utf-8")
    marker = source.index("-- migrate:up")
    start = source.index("\n", marker) + 1
    end = source.index("-- migrate:down", start)
    return source[start:end]


def _replay_migrations(database_name: str) -> None:
    migration_paths = sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda path: path.name)
    with _connect(database_name) as connection:
        connection.execute(
            """
            CREATE TABLE public.schema_migrations (
                version varchar PRIMARY KEY
            )
            """
        )
        for path in migration_paths:
            connection.execute(_migration_up(path.name))
            connection.execute(
                "INSERT INTO public.schema_migrations (version) VALUES (%s)",
                (path.name.split("_", maxsplit=1)[0],),
            )


def _schema_signature(connection: psycopg.Connection) -> dict[str, list[tuple[Any, ...]]]:
    tables = connection.execute(
        """
        SELECT c.relname
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
        ORDER BY c.relname
        """
    ).fetchall()
    columns = connection.execute(
        """
        SELECT
            c.relname,
            a.attname,
            pg_catalog.format_type(a.atttypid, a.atttypmod),
            a.attnotnull,
            pg_catalog.pg_get_expr(d.adbin, d.adrelid)
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
        LEFT JOIN pg_catalog.pg_attrdef d
          ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY c.relname, a.attname
        """
    ).fetchall()
    indexes = connection.execute(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_catalog.pg_indexes
        WHERE schemaname = 'public'
        ORDER BY tablename, indexname
        """
    ).fetchall()
    constraints = connection.execute(
        """
        SELECT
            c.relname,
            con.conname,
            con.contype,
            con.condeferrable,
            con.condeferred,
            con.convalidated,
            pg_catalog.pg_get_constraintdef(con.oid, true)
        FROM pg_catalog.pg_constraint con
        JOIN pg_catalog.pg_class c ON c.oid = con.conrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND con.contype <> 'n'
        ORDER BY c.relname, con.conname
        """
    ).fetchall()
    return {
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "constraints": constraints,
    }


def _assert_matching_signatures(
    actual: dict[str, list[tuple[Any, ...]]],
    expected: dict[str, list[tuple[Any, ...]]],
) -> None:
    for category in ("tables", "columns", "indexes", "constraints"):
        actual_rows = set(actual[category])
        expected_rows = set(expected[category])
        assert actual_rows == expected_rows, (
            f"{category} differ:\n"
            f"missing from replay: {sorted(expected_rows - actual_rows)!r}\n"
            f"extra in replay: {sorted(actual_rows - expected_rows)!r}"
        )


def test_full_migration_replay_matches_schema_snapshot() -> None:
    with _isolated_databases(2) as (schema_database, replay_database):
        _load_schema(schema_database)
        _replay_migrations(replay_database)

        with _connect(schema_database) as schema_connection:
            expected = _schema_signature(schema_connection)
        with _connect(replay_database) as replay_connection:
            actual = _schema_signature(replay_connection)

    _assert_matching_signatures(actual, expected)


def test_backfilled_baseline_is_applied_without_changing_existing_schema() -> None:
    with _isolated_databases(1) as (database_name,):
        _load_schema(database_name)
        with _connect(database_name) as connection:
            connection.execute(
                "DELETE FROM public.schema_migrations WHERE version = %s",
                (BASELINE_VERSION,),
            )
            before = _schema_signature(connection)

        output = _run_dbmate(database_name, "up")

        with _connect(database_name) as connection:
            applied = connection.execute(
                "SELECT count(*) FROM public.schema_migrations WHERE version = %s",
                (BASELINE_VERSION,),
            ).fetchone()
            after = _schema_signature(connection)

    assert applied == (1,)
    assert f"Applied: {BASELINE_FILENAME}" in output
    assert after == before


def test_backdated_migrations_are_noops_when_target_tables_do_not_exist() -> None:
    with _isolated_databases(1) as (database_name,):
        with _connect(database_name) as connection:
            for filename in BACKDATED_MIGRATIONS:
                connection.execute(_migration_up(filename))
            tables = connection.execute(
                """
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            ).fetchall()

    assert tables == []
