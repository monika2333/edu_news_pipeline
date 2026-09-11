from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row

from src.adapters.db_postgres_app_config import (
    ConfigTargetNotEmptyError,
)
from src.adapters.db_postgres_core import PostgresAdapter
from src.business_config import load_business_config
from src.config import get_settings
from src.console import settings_service
from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "database"
    / "migrations"
    / "20260911100000_add_console_managed_settings.sql"
)
ADMIN_ID = "00000000-0000-0000-0000-000000000201"


@contextmanager
def _isolated_database() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    database_name = f"test_console_settings_{uuid4().hex}"
    maintenance = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=True,
    )
    maintenance.execute(
        sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
    )
    connection = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=database_name,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        yield connection
    finally:
        connection.close()
        maintenance.execute(
            sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )
        maintenance.close()


def _migration_parts() -> tuple[str, str]:
    source = MIGRATION_PATH.read_text(encoding="utf-8")
    up, down = source.split("-- migrate:down", maxsplit=1)
    return up.split("-- migrate:up", maxsplit=1)[1], down


def _create_legacy_schema(connection: psycopg.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE public.console_users (
            id uuid PRIMARY KEY,
            display_name text NOT NULL
        );
        CREATE TABLE public.pipeline_runs (
            id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
            run_id text NOT NULL UNIQUE,
            status text NOT NULL DEFAULT 'running',
            trigger_source text,
            plan jsonb,
            started_at timestamptz NOT NULL DEFAULT now(),
            finished_at timestamptz,
            steps_completed integer NOT NULL DEFAULT 0,
            artifacts jsonb,
            error_summary text,
            updated_at timestamptz NOT NULL DEFAULT now()
        );
        """
    )
    connection.execute(
        "INSERT INTO public.console_users (id, display_name) VALUES (%s, %s)",
        (ADMIN_ID, "配置管理员"),
    )


def _sections() -> dict[str, object]:
    return {
        "llm_models": {
            "default": "model-a",
            "steps": {
                step: {"model": None, "reasoning": step != "summary"}
                for step in (
                    "summary",
                    "source",
                    "sentiment",
                    "scoring",
                    "external_filter",
                    "beijing_gate",
                    "duplicate_review",
                )
            },
        },
        "crawl_sources": ["toutiao", "tencent"],
    }


def _account(identifier: str = "account-1") -> dict[str, object]:
    return {
        "source": "toutiao",
        "normalized_identifier": identifier,
        "original_input": identifier,
        "profile_url": f"https://www.toutiao.com/c/user/token/{identifier}/",
        "display_name": None,
        "enabled": True,
    }


def _admin_user() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id=ADMIN_ID,
        username="admin",
        display_name="配置管理员",
        role="admin",
    )


def test_migration_up_and_down_create_all_configuration_storage() -> None:
    up_sql, down_sql = _migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(up_sql)

        tables = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            ORDER BY table_name
            """,
            (["app_settings", "crawl_accounts"],),
        ).fetchall()
        snapshot_column = connection.execute(
            """
            SELECT data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'pipeline_runs'
              AND column_name = 'config_snapshot'
            """
        ).fetchone()
        unique_definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = 'public.crawl_accounts'::regclass
              AND conname = 'crawl_accounts_source_identifier_unique'
            """
        ).fetchone()["definition"]

        assert [row["table_name"] for row in tables] == [
            "app_settings",
            "crawl_accounts",
        ]
        assert snapshot_column == {"data_type": "jsonb", "is_nullable": "YES"}
        assert "UNIQUE (source, normalized_identifier)" in unique_definition

        connection.execute(down_sql)
        remaining_tables = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            """,
            (["app_settings", "crawl_accounts"],),
        ).fetchall()
        remaining_snapshot = connection.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'pipeline_runs'
              AND column_name = 'config_snapshot'
            """
        ).fetchone()

        assert remaining_tables == []
        assert remaining_snapshot is None


def test_m5_stale_put_returns_409_and_keeps_current_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    up_sql, _down_sql = _migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(up_sql)
        adapter = PostgresAdapter(connection)
        adapter.import_app_config(
            sections=_sections(),
            accounts=[],
        )
        adapter.update_app_setting_as_user(
            section="crawl_sources",
            value=["tencent", "toutiao"],
            expected_version=1,
            actor_user_id=ADMIN_ID,
        )
        monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)
        monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
        app = create_app()
        app.dependency_overrides[require_console_user] = _admin_user

        response = TestClient(app).put(
            "/api/admin/settings/crawl_sources",
            json={"value": ["toutiao"], "expected_version": 1},
        )

        current = adapter.app_config.fetch_setting("crawl_sources")
        assert response.status_code == 409
        assert current["version"] == 2
        assert current["value"] == ["tencent", "toutiao"]
        assert str(current["updated_by_user_id"]) == ADMIN_ID


@pytest.mark.parametrize("occupied_target", ["settings", "accounts"])
def test_m13_import_refuses_when_any_target_already_has_data(
    occupied_target: str,
) -> None:
    up_sql, _down_sql = _migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(up_sql)
        adapter = PostgresAdapter(connection)
        if occupied_target == "settings":
            connection.execute(
                """
                INSERT INTO app_settings (section, value)
                VALUES ('crawl_sources', '["toutiao"]'::jsonb)
                """
            )
        else:
            connection.execute(
                """
                INSERT INTO crawl_accounts (
                    source, normalized_identifier, original_input, profile_url
                )
                VALUES ('toutiao', 'existing', 'existing', 'https://example.test')
                """
            )

        with pytest.raises(ConfigTargetNotEmptyError):
            adapter.import_app_config(
                sections=_sections(),
                accounts=[_account("account-2")],
            )

        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM app_settings) AS settings_count,
                (SELECT count(*) FROM crawl_accounts) AS accounts_count
            """
        ).fetchone()
        assert counts == {
            "settings_count": 1 if occupied_target == "settings" else 0,
            "accounts_count": 1 if occupied_target == "accounts" else 0,
        }


def test_m10_runtime_query_excludes_disabled_accounts() -> None:
    up_sql, _down_sql = _migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(up_sql)
        adapter = PostgresAdapter(connection)
        adapter.import_app_config(
            sections=_sections(),
            accounts=[
                _account("enabled-account"),
                {**_account("disabled-account"), "enabled": False},
            ],
        )

        loaded = load_business_config(adapter)

        assert [
            account.normalized_identifier
            for account in loaded.accounts["toutiao"]
        ] == ["enabled-account"]


def test_m18_process_adapter_persists_the_effective_config_snapshot() -> None:
    up_sql, _down_sql = _migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(up_sql)
        adapter = PostgresAdapter(connection)
        snapshot = {
            "llm_models": {
                "summary": {"model": "model-a", "reasoning": False}
            },
            "crawl_sources": ["bjrb"],
            "crawl_accounts": {"toutiao": ["account-1"]},
            "versions": {"llm_models": 3, "crawl_sources": 5},
        }

        adapter.process.record_pipeline_run_start(
            run_id="snapshot-run",
            started_at=datetime.now(timezone.utc),
            plan=["crawl"],
            trigger_source="test",
            config_snapshot=snapshot,
        )

        row = connection.execute(
            "SELECT plan, config_snapshot FROM pipeline_runs WHERE run_id = %s",
            ("snapshot-run",),
        ).fetchone()
        assert row["plan"] == ["crawl"]
        assert row["config_snapshot"] == snapshot
