from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row

from src.adapters.db_postgres_core import PostgresAdapter
from src.business_config import (
    REVIEW_SORT_KEYWORD_DEFAULTS,
    load_business_config,
)
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
ACCOUNT_NAMES_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "database"
    / "migrations"
    / "20260913120000_sync_crawl_account_display_names.sql"
)
ENDPOINTS_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "database"
    / "migrations"
    / "20260918120000_add_llm_endpoints_setting.sql"
)
SORT_KEYWORDS_MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "database"
    / "migrations"
    / "20260923120000_add_review_sort_keywords_setting.sql"
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


def _account_names_migration_parts() -> tuple[str, str]:
    source = ACCOUNT_NAMES_MIGRATION_PATH.read_text(encoding="utf-8")
    up, down = source.split("-- migrate:down", maxsplit=1)
    return up.split("-- migrate:up", maxsplit=1)[1], down


def _endpoints_migration_parts() -> tuple[str, str]:
    source = ENDPOINTS_MIGRATION_PATH.read_text(encoding="utf-8")
    up, down = source.split("-- migrate:down", maxsplit=1)
    return up.split("-- migrate:up", maxsplit=1)[1], down


def _sort_keywords_migration_parts() -> tuple[str, str]:
    source = SORT_KEYWORDS_MIGRATION_PATH.read_text(encoding="utf-8")
    up, down = source.split("-- migrate:down", maxsplit=1)
    return up.split("-- migrate:up", maxsplit=1)[1], down


def _apply_managed_setting_migrations(connection: psycopg.Connection) -> None:
    """The migrations a fresh deployment applies before the one-shot import:
    console-managed settings, then the migration-seeded rows (llm_endpoints、
    review_sort_keywords——两者都没有旧配置文件，不进一次性导入)。"""

    base_up, _base_down = _migration_parts()
    endpoints_up, _endpoints_down = _endpoints_migration_parts()
    sort_keywords_up, _sort_keywords_down = _sort_keywords_migration_parts()
    connection.execute(base_up)
    connection.execute(endpoints_up)
    connection.execute(sort_keywords_up)


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
    """Exactly what ``preview_legacy_import`` produces: llm_endpoints and
    review_sort_keywords are seeded by their own migrations and never part
    of the one-shot import."""

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
        "score_keyword_bonuses": [
            {"keyword": "教育工委", "bonus": 100},
            {"keyword": "高考", "bonus": 10},
        ],
        "education_keywords": ["教育", "学校"],
        "beijing_keywords": ["北京", "海淀"],
        "source_aliases": {"suffixes": ["客户端"], "aliases": {"北京号": "北京日报"}},
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


def test_llm_endpoints_migration_seeds_openrouter_and_down_removes_it() -> None:
    base_up, _base_down = _migration_parts()
    up_sql, down_sql = _endpoints_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(base_up)
        connection.execute(up_sql)

        row = connection.execute(
            "SELECT value, version, updated_by_user_id FROM app_settings WHERE section = 'llm_endpoints'"
        ).fetchone()
        assert row["updated_by_user_id"] is None
        assert row["version"] == 1
        value = row["value"]
        assert value["default"] == "openrouter"
        assert value["items"] == [
            {
                "key": "openrouter",
                "label": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "LLM_API_KEY",
                "api_style": "openrouter",
                "temperature_override": None,
            }
        ]

        connection.execute(down_sql)
        remaining = connection.execute(
            "SELECT 1 FROM app_settings WHERE section = 'llm_endpoints'"
        ).fetchone()
        assert remaining is None


def test_review_sort_keywords_migration_seeds_defaults_and_down_removes_it() -> None:
    base_up, _base_down = _migration_parts()
    up_sql, down_sql = _sort_keywords_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(base_up)
        connection.execute(up_sql)

        row = connection.execute(
            "SELECT value, version, updated_by_user_id FROM app_settings"
            " WHERE section = 'review_sort_keywords'"
        ).fetchone()
        assert row["updated_by_user_id"] is None
        assert row["version"] == 1
        # 播种内容必须与代码默认词表一致：迁移是初始值的唯一权威来源
        assert row["value"] == {
            category: list(words)
            for category, words in REVIEW_SORT_KEYWORD_DEFAULTS.items()
        }

        connection.execute(down_sql)
        remaining = connection.execute(
            "SELECT 1 FROM app_settings WHERE section = 'review_sort_keywords'"
        ).fetchone()
        assert remaining is None


def test_fresh_deploy_migrations_then_import_loads_full_config() -> None:
    """The real fresh-deployment order: dbmate up applies every migration
    (seeded llm_endpoints included), then `import-settings --apply` writes
    everything through the incremental path. On the empty database the
    accounts must actually be written; re-running the import must skip every
    section without changing data or accounts."""

    base_up, _base_down = _migration_parts()
    names_up, _names_down = _account_names_migration_parts()
    endpoints_up, _endpoints_down = _endpoints_migration_parts()
    sort_keywords_up, _sort_keywords_down = _sort_keywords_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(base_up)
        connection.execute(names_up)
        connection.execute(endpoints_up)
        connection.execute(sort_keywords_up)

        adapter = PostgresAdapter(connection)
        report = adapter.import_app_config_missing(
            sections=_sections(),
            accounts=[_account("account-1")],
        )

        assert sorted(report["written_sections"]) == sorted(_sections())
        assert report["skipped_sections"] == []
        assert report["accounts_written"] is True

        loaded = load_business_config(adapter)
        assert loaded.llm_steps["summary"].model == "model-a"
        assert loaded.crawl_sources == ("toutiao", "tencent")
        assert loaded.score_bonus_rules() == {"教育工委": 100, "高考": 10}
        assert loaded.education_keywords == ("教育", "学校")
        assert loaded.beijing_keywords == ("北京", "海淀")
        assert loaded.source_aliases.suffixes == ("客户端",)
        assert loaded.review_sort_keywords == {
            category: tuple(words)
            for category, words in REVIEW_SORT_KEYWORD_DEFAULTS.items()
        }
        assert [
            account.normalized_identifier for account in loaded.accounts["toutiao"]
        ] == ["account-1"]
        assert loaded.endpoint_for_step("summary").key == "openrouter"
        assert loaded.endpoint_for_step("duplicate_review").key == "openrouter"

        seed = connection.execute(
            "SELECT value, version FROM app_settings WHERE section = 'llm_endpoints'"
        ).fetchone()
        assert seed["version"] == 1
        assert seed["value"]["default"] == "openrouter"
        imported_sections = connection.execute(
            "SELECT section FROM app_settings ORDER BY section"
        ).fetchall()
        assert [row["section"] for row in imported_sections] == [
            "beijing_keywords",
            "crawl_sources",
            "education_keywords",
            "llm_endpoints",
            "llm_models",
            "review_sort_keywords",
            "score_keyword_bonuses",
            "source_aliases",
        ]

        # 重复导入：全部分区跳过，版本与内容一字不变，账号数不变
        def _snapshot_rows() -> dict[str, Any]:
            return {
                row["section"]: (row["value"], int(row["version"]))
                for row in connection.execute(
                    "SELECT section, value, version FROM app_settings"
                ).fetchall()
            }

        before_rerun = _snapshot_rows()
        rerun = adapter.import_app_config_missing(
            sections=_sections(),
            accounts=[_account("account-1")],
        )
        assert rerun["written_sections"] == []
        assert sorted(rerun["skipped_sections"]) == sorted(_sections())
        assert rerun["accounts_written"] is False
        assert _snapshot_rows() == before_rerun
        accounts_count = connection.execute(
            "SELECT count(*) AS n FROM crawl_accounts"
        ).fetchone()["n"]
        assert accounts_count == 1


def test_phase2_import_writes_only_missing_sections_on_populated_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """M9: the phase-2 import runs against a database that already went
    through phase 1 (plus the migration-seeded rows that a deployment of
    this era always has). Existing sections keep version and content
    untouched, ``crawl_accounts`` keeps its row count, and only the four
    wordlist sections are written."""

    base_up, _base_down = _migration_parts()
    names_up, _names_down = _account_names_migration_parts()
    endpoints_up, _endpoints_down = _endpoints_migration_parts()
    sort_keywords_up, _sort_keywords_down = _sort_keywords_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(base_up)
        connection.execute(names_up)
        connection.execute(endpoints_up)
        connection.execute(sort_keywords_up)
        adapter = PostgresAdapter(connection)

        phase1_sections = {
            key: value
            for key, value in _sections().items()
            if key in ("llm_models", "crawl_sources")
        }
        adapter.import_app_config_missing(
            sections=phase1_sections,
            accounts=[_account("account-1")],
        )
        # 一期导入之后管理员又改过一次 llm_models：版本号推进到 2
        adapter.app_config.update_app_setting_as_user(
            section="llm_models",
            value=phase1_sections["llm_models"],
            expected_version=1,
            actor_user_id=ADMIN_ID,
        )
        before_import = adapter.app_config.fetch_setting("llm_models")
        assert int(before_import["version"]) == 2

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "education_keywords.txt").write_text(
            "教育\n学校\n", encoding="utf-8"
        )
        (config_dir / "beijing_keywords.txt").write_text("北京\n海淀\n", encoding="utf-8")
        (config_dir / "source_aliases.json").write_text(
            json.dumps(
                {"suffixes": ["客户端"], "aliases": {"北京号": "北京日报"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (config_dir / "score_keyword_bonuses.json").write_text(
            json.dumps({"教育工委": 100}, ensure_ascii=False),
            encoding="utf-8",
        )
        # 一期遗留的旧账号文件：一个账号还在库里，另一个早已在控制台删除。
        # 导入必须保持"表非空则不写账号"，不能把删掉的账号复活。
        (config_dir / "toutiao_author.txt").write_text(
            "account-1\naccount-9\n", encoding="utf-8"
        )

        monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)
        monkeypatch.setattr(settings_service, "load_environment", lambda: None)
        for key in (
            "CRAWL_SOURCES",
            "SCORE_KEYWORD_BONUSES",
            "KEYWORDS_PATH",
            "BEIJING_KEYWORDS_PATH",
            "SOURCE_ALIASES_PATH",
            "SCORE_KEYWORD_BONUSES_PATH",
            "TOUTIAO_AUTHORS_PATH",
        ):
            monkeypatch.delenv(key, raising=False)

        report = settings_service.import_legacy_config(apply=True, root=tmp_path)

        assert report["written_sections"] == [
            "score_keyword_bonuses",
            "education_keywords",
            "beijing_keywords",
            "source_aliases",
        ]
        assert report["skipped_sections"] == ["llm_models", "crawl_sources"]
        assert report["accounts_written"] is False

        # M9: 已有分区的版本号和内容都不变
        after_import = adapter.app_config.fetch_setting("llm_models")
        assert int(after_import["version"]) == int(before_import["version"])
        assert after_import["value"] == before_import["value"]
        sources_after = adapter.app_config.fetch_setting("crawl_sources")
        assert sources_after["value"] == ["toutiao", "tencent"]
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM app_settings) AS settings_count,
                (SELECT count(*) FROM crawl_accounts) AS accounts_count
            """
        ).fetchone()
        assert counts == {"settings_count": 8, "accounts_count": 1}
        # 旧账号文件里库里没有的 account-9 不得被写入
        revived = connection.execute(
            "SELECT 1 FROM crawl_accounts WHERE normalized_identifier = 'account-9'"
        ).fetchone()
        assert revived is None

        # 导入完成后整份配置可加载，词表内容与文件一致
        loaded = load_business_config(adapter)
        assert loaded.education_keywords == ("教育", "学校")
        assert loaded.beijing_keywords == ("北京", "海淀")
        assert loaded.score_bonus_rules() == {"教育工委": 100}
        assert loaded.source_aliases.aliases == {"北京号": "北京日报"}


def test_account_name_columns_migration_up_and_down() -> None:
    base_up, _base_down = _migration_parts()
    up_sql, down_sql = _account_names_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(base_up)

        connection.execute(up_sql)
        columns = connection.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'crawl_accounts'
              AND column_name = ANY(%s)
            ORDER BY column_name
            """,
            (["display_name_error", "display_name_synced_at"],),
        ).fetchall()
        assert columns == [
            {
                "column_name": "display_name_error",
                "data_type": "text",
                "is_nullable": "YES",
            },
            {
                "column_name": "display_name_synced_at",
                "data_type": "timestamp with time zone",
                "is_nullable": "YES",
            },
        ]

        connection.execute(down_sql)
        remaining = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'crawl_accounts'
              AND column_name = ANY(%s)
            """,
            (["display_name_error", "display_name_synced_at"],),
        ).fetchall()
        assert remaining == []


def test_m5_stale_put_returns_409_and_keeps_current_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    up_sql, _down_sql = _migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(up_sql)
        adapter = PostgresAdapter(connection)
        adapter.import_app_config_missing(
            sections=_sections(),
            accounts=[],
        )
        adapter.app_config.update_app_setting_as_user(
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


def test_m10_runtime_query_excludes_disabled_accounts() -> None:
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        _apply_managed_setting_migrations(connection)
        adapter = PostgresAdapter(connection)
        adapter.import_app_config_missing(
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


def test_single_table_config_writes_are_exposed_only_by_namespace() -> None:
    names_up, _names_down = _account_names_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        _apply_managed_setting_migrations(connection)
        connection.execute(names_up)
        adapter = PostgresAdapter(connection)
        adapter.import_app_config_missing(sections=_sections(), accounts=[])

        created = adapter.app_config.create_crawl_account_as_user(
            source="toutiao",
            normalized_identifier="one",
            original_input="one",
            profile_url="https://example.test/one",
            display_name=None,
            actor_user_id=ADMIN_ID,
        )
        bulk_created = adapter.app_config.create_crawl_accounts_as_user(
            accounts=[
                {
                    "source": "toutiao",
                    "normalized_identifier": "two",
                    "original_input": "two",
                    "profile_url": "https://example.test/two",
                }
            ],
            actor_user_id=ADMIN_ID,
        )
        updated = adapter.app_config.update_crawl_account_as_user(
            account_id=str(created["id"]),
            enabled=False,
            set_enabled=True,
            actor_user_id=ADMIN_ID,
        )
        named = adapter.app_config.record_crawl_account_name_success(
            account_id=str(created["id"]),
            display_name="账号一",
        )
        deleted = adapter.app_config.delete_crawl_account_as_user(
            account_id=str(bulk_created[0]["id"]),
        )

        assert updated["enabled"] is False
        assert named["display_name"] == "账号一"
        assert named["display_name_synced_at"] is not None
        assert deleted["normalized_identifier"] == "two"
        assert not hasattr(adapter, "create_crawl_account_as_user")
        assert not hasattr(adapter, "delete_crawl_account_as_user")


def test_m1_name_failure_preserves_existing_name_and_m8_system_writes_keep_modifier() -> None:
    base_up, _base_down = _migration_parts()
    names_up, _names_down = _account_names_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(base_up)
        connection.execute(names_up)
        adapter = PostgresAdapter(connection)
        created = adapter.app_config.create_crawl_account_as_user(
            source="toutiao",
            normalized_identifier="one",
            original_input="one",
            profile_url="https://example.test/account",
            display_name="人工旧名",
            actor_user_id=ADMIN_ID,
        )

        failed = adapter.app_config.record_crawl_account_name_failure(
            account_id=str(created["id"]),
            error="页面里没有找到账号名",
        )
        resolved = adapter.app_config.record_crawl_account_name_success(
            account_id=str(created["id"]),
            display_name="系统真名",
        )

        assert failed["display_name"] == "人工旧名"
        assert failed["display_name_synced_at"] is None
        assert str(failed["updated_by_user_id"]) == ADMIN_ID
        assert resolved["display_name"] == "系统真名"
        assert resolved["display_name_error"] is None
        assert str(resolved["updated_by_user_id"]) == ADMIN_ID


def test_m5_article_name_fallback_matches_token_and_uses_latest_nonempty_source() -> None:
    base_up, _base_down = _migration_parts()
    names_up, _names_down = _account_names_migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(base_up)
        connection.execute(names_up)
        connection.execute(
            """
            CREATE TABLE raw_articles (
                token text,
                profile_url text,
                article_id text PRIMARY KEY,
                source text,
                fetched_at timestamptz NOT NULL,
                created_at timestamptz NOT NULL
            )
            """
        )
        adapter = PostgresAdapter(connection)
        adapter.app_config.create_crawl_account_as_user(
            source="toutiao",
            normalized_identifier="token-1",
            original_input="token-1",
            profile_url="https://example.test/account-profile",
            display_name=None,
            actor_user_id=ADMIN_ID,
        )
        connection.execute(
            """
            INSERT INTO raw_articles (
                token, profile_url, article_id, source, fetched_at, created_at
            ) VALUES
                ('token-1', 'https://example.test/not-account', 'old', '旧名称', '2026-01-01', '2026-01-01'),
                ('token-1', 'https://example.test/not-account', 'new', '新名称', '2026-02-01', '2026-02-01'),
                ('token-1', 'https://example.test/not-account', 'empty', '   ', '2026-03-01', '2026-03-01')
            """
        )

        assert adapter.app_config.fetch_latest_account_article_source(
            source="toutiao",
            normalized_identifier="token-1",
        ) == "新名称"
        assert adapter.app_config.fetch_latest_account_article_source(
            source="btime",
            normalized_identifier="token-1",
        ) is None
        assert adapter.app_config.fetch_latest_account_article_source(
            source="beijinghao",
            normalized_identifier="token-1",
        ) is None


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
