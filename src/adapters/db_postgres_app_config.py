from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Optional, Sequence

import psycopg
from psycopg.types.json import Json

if TYPE_CHECKING:
    from src.adapters.db_postgres_core import PostgresAdapter


class ConfigVersionConflictError(RuntimeError):
    """Raised when an app setting was changed after a client read it."""


class ConfigTargetNotEmptyError(RuntimeError):
    """Raised when a one-time import would overwrite database configuration."""


class CrawlAccountConflictError(RuntimeError):
    """Raised when a source already has the normalized account identifier."""


class AppConfigNamespace:
    """Access to console-managed business configuration."""

    def __init__(self, adapter: PostgresAdapter) -> None:
        self._adapter = adapter

    def fetch_settings(self) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_settings(cur)

    def fetch_setting(self, section: str) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_setting(cur, section)

    def fetch_enabled_accounts(
        self,
        source: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_enabled_accounts(cur, source=source)

    def fetch_accounts(self, source: str) -> list[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_accounts(cur, source)

    def fetch_account(self, account_id: str) -> Optional[dict[str, Any]]:
        with self._adapter._cursor() as cur:
            return fetch_account(cur, account_id)

    def fetch_latest_account_article_source(
        self,
        *,
        source: str,
        normalized_identifier: str,
    ) -> Optional[str]:
        with self._adapter._cursor() as cur:
            return fetch_latest_account_article_source(
                cur,
                source=source,
                normalized_identifier=normalized_identifier,
            )

    def update_app_setting_as_user(
        self,
        *,
        section: str,
        value: Any,
        expected_version: int,
        actor_user_id: str,
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return update_setting(
                cur,
                section=section,
                value=value,
                expected_version=expected_version,
                actor_user_id=actor_user_id,
            )

    def create_crawl_account_as_user(
        self,
        *,
        source: str,
        normalized_identifier: str,
        original_input: str,
        profile_url: str,
        display_name: Optional[str],
        actor_user_id: str,
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return insert_account(
                cur,
                source=source,
                normalized_identifier=normalized_identifier,
                original_input=original_input,
                profile_url=profile_url,
                display_name=display_name,
                enabled=True,
                actor_user_id=actor_user_id,
            )

    def create_crawl_accounts_as_user(
        self,
        *,
        accounts: Sequence[Mapping[str, Any]],
        actor_user_id: str,
    ) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        with self._adapter.transaction() as cur:
            for item in accounts:
                created.append(
                    insert_account(
                        cur,
                        source=str(item["source"]),
                        normalized_identifier=str(item["normalized_identifier"]),
                        original_input=str(item["original_input"]),
                        profile_url=str(item["profile_url"]),
                        display_name=item.get("display_name"),
                        enabled=True,
                        actor_user_id=actor_user_id,
                    )
                )
        return created

    def update_crawl_account_as_user(
        self,
        *,
        account_id: str,
        enabled: Optional[bool],
        set_enabled: bool,
        actor_user_id: str,
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return update_account(
                cur,
                account_id=account_id,
                enabled=enabled,
                set_enabled=set_enabled,
                actor_user_id=actor_user_id,
            )

    def record_crawl_account_name_success(
        self,
        *,
        account_id: str,
        display_name: str,
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return record_account_name_success(
                cur,
                account_id=account_id,
                display_name=display_name,
            )

    def record_crawl_account_name_failure(
        self,
        *,
        account_id: str,
        error: str,
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return record_account_name_failure(
                cur,
                account_id=account_id,
                error=error,
            )

    def delete_crawl_account_as_user(
        self,
        *,
        account_id: str,
    ) -> dict[str, Any]:
        with self._adapter.transaction() as cur:
            return delete_account(cur, account_id)


def fetch_settings(cur: psycopg.Cursor) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT s.*, u.display_name AS updated_by_display_name
        FROM app_settings s
        LEFT JOIN console_users u ON u.id = s.updated_by_user_id
        ORDER BY s.section
        """
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_setting(
    cur: psycopg.Cursor,
    section: str,
    *,
    for_update: bool = False,
) -> Optional[dict[str, Any]]:
    suffix = " FOR UPDATE OF s" if for_update else ""
    cur.execute(
        f"""
        SELECT s.*, u.display_name AS updated_by_display_name
        FROM app_settings s
        LEFT JOIN console_users u ON u.id = s.updated_by_user_id
        WHERE s.section = %s{suffix}
        """,
        (section,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def update_setting(
    cur: psycopg.Cursor,
    *,
    section: str,
    value: Any,
    expected_version: int,
    actor_user_id: str,
) -> dict[str, Any]:
    before = fetch_setting(cur, section, for_update=True)
    if before is None:
        raise KeyError(section)
    if int(before["version"]) != expected_version:
        raise ConfigVersionConflictError(
            f"配置版本冲突：当前版本为 {before['version']}"
        )
    cur.execute(
        """
        UPDATE app_settings
        SET value = %s,
            version = version + 1,
            updated_at = now(),
            updated_by_user_id = %s
        WHERE section = %s
        RETURNING *
        """,
        (Json(value), actor_user_id, section),
    )
    return dict(cur.fetchone())


def fetch_enabled_accounts(
    cur: psycopg.Cursor,
    *,
    source: Optional[str] = None,
) -> list[dict[str, Any]]:
    conditions = ["enabled"]
    params: list[Any] = []
    if source is not None:
        conditions.append("source = %s")
        params.append(source)
    cur.execute(
        f"""
        SELECT *
        FROM crawl_accounts
        WHERE {' AND '.join(conditions)}
        ORDER BY source, created_at, id
        """,
        params,
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_accounts(cur: psycopg.Cursor, source: str) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT a.*,
               creator.display_name AS created_by_display_name,
               updater.display_name AS updated_by_display_name
        FROM crawl_accounts a
        LEFT JOIN console_users creator ON creator.id = a.created_by_user_id
        LEFT JOIN console_users updater ON updater.id = a.updated_by_user_id
        WHERE a.source = %s
        ORDER BY a.created_at, a.id
        """,
        (source,),
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_account(
    cur: psycopg.Cursor,
    account_id: str,
    *,
    for_update: bool = False,
) -> Optional[dict[str, Any]]:
    suffix = " FOR UPDATE OF a" if for_update else ""
    cur.execute(
        f"SELECT a.* FROM crawl_accounts a WHERE a.id = %s{suffix}",
        (account_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def find_account(
    cur: psycopg.Cursor,
    *,
    source: str,
    normalized_identifier: str,
) -> Optional[dict[str, Any]]:
    cur.execute(
        """
        SELECT *
        FROM crawl_accounts
        WHERE source = %s AND normalized_identifier = %s
        """,
        (source, normalized_identifier),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def fetch_latest_account_article_source(
    cur: psycopg.Cursor,
    *,
    source: str,
    normalized_identifier: str,
) -> Optional[str]:
    if source not in {"toutiao", "tencent"}:
        return None
    cur.execute(
        """
        SELECT r.source
        FROM raw_articles r
        JOIN crawl_accounts a
          ON a.normalized_identifier = r.token
        WHERE a.source = %s
          AND a.normalized_identifier = %s
          AND r.source IS NOT NULL
          AND btrim(r.source) <> ''
        ORDER BY r.fetched_at DESC, r.created_at DESC, r.article_id DESC
        LIMIT 1
        """,
        (source, normalized_identifier),
    )
    row = cur.fetchone()
    return str(row["source"]).strip() if row else None


def insert_account(
    cur: psycopg.Cursor,
    *,
    source: str,
    normalized_identifier: str,
    original_input: str,
    profile_url: str,
    display_name: Optional[str],
    enabled: bool,
    actor_user_id: Optional[str],
) -> dict[str, Any]:
    try:
        cur.execute(
            """
            INSERT INTO crawl_accounts (
                source, normalized_identifier, original_input, profile_url,
                display_name, enabled, created_by_user_id, updated_by_user_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            (
                source,
                normalized_identifier,
                original_input,
                profile_url,
                display_name,
                enabled,
                actor_user_id,
                actor_user_id,
            ),
        )
    except psycopg.errors.UniqueViolation as exc:
        raise CrawlAccountConflictError(
            f"{source} 已存在账号 {normalized_identifier}"
        ) from exc
    return dict(cur.fetchone())


def update_account(
    cur: psycopg.Cursor,
    *,
    account_id: str,
    enabled: Optional[bool],
    set_enabled: bool,
    actor_user_id: str,
) -> dict[str, Any]:
    if fetch_account(cur, account_id, for_update=True) is None:
        raise KeyError(account_id)
    cur.execute(
        """
        UPDATE crawl_accounts
        SET enabled = CASE WHEN %s THEN %s ELSE enabled END,
            updated_at = now(),
            updated_by_user_id = %s
        WHERE id = %s
        RETURNING *
        """,
        (
            set_enabled,
            enabled,
            actor_user_id,
            account_id,
        ),
    )
    return dict(cur.fetchone())


def record_account_name_success(
    cur: psycopg.Cursor,
    *,
    account_id: str,
    display_name: str,
) -> dict[str, Any]:
    if fetch_account(cur, account_id, for_update=True) is None:
        raise KeyError(account_id)
    cur.execute(
        """
        UPDATE crawl_accounts
        SET display_name = %s,
            display_name_synced_at = now(),
            display_name_error = NULL,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (display_name, account_id),
    )
    return dict(cur.fetchone())


def record_account_name_failure(
    cur: psycopg.Cursor,
    *,
    account_id: str,
    error: str,
) -> dict[str, Any]:
    if fetch_account(cur, account_id, for_update=True) is None:
        raise KeyError(account_id)
    cur.execute(
        """
        UPDATE crawl_accounts
        SET display_name_error = %s,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (error, account_id),
    )
    return dict(cur.fetchone())


def delete_account(cur: psycopg.Cursor, account_id: str) -> dict[str, Any]:
    before = fetch_account(cur, account_id, for_update=True)
    if before is None:
        raise KeyError(account_id)
    cur.execute("DELETE FROM crawl_accounts WHERE id = %s", (account_id,))
    return before


def import_config_bundle(
    cur: psycopg.Cursor,
    *,
    sections: Mapping[str, Any],
    accounts: Sequence[Mapping[str, Any]],
) -> None:
    cur.execute("LOCK TABLE app_settings, crawl_accounts IN SHARE ROW EXCLUSIVE MODE")
    cur.execute("SELECT EXISTS (SELECT 1 FROM app_settings) AS occupied")
    settings_occupied = bool(cur.fetchone()["occupied"])
    cur.execute("SELECT EXISTS (SELECT 1 FROM crawl_accounts) AS occupied")
    accounts_occupied = bool(cur.fetchone()["occupied"])
    if settings_occupied or accounts_occupied:
        raise ConfigTargetNotEmptyError("数据库配置目标已有数据，拒绝导入")

    for section, value in sections.items():
        cur.execute(
            """
            INSERT INTO app_settings (section, value, version)
            VALUES (%s, %s, 1)
            """,
            (section, Json(value)),
        )

    for item in accounts:
        insert_account(
            cur,
            source=str(item["source"]),
            normalized_identifier=str(item["normalized_identifier"]),
            original_input=str(item["original_input"]),
            profile_url=str(item["profile_url"]),
            display_name=item.get("display_name"),
            enabled=bool(item.get("enabled", True)),
            actor_user_id=None,
        )


__all__ = [
    "AppConfigNamespace",
    "ConfigTargetNotEmptyError",
    "ConfigVersionConflictError",
    "CrawlAccountConflictError",
    "delete_account",
    "fetch_account",
    "fetch_accounts",
    "fetch_enabled_accounts",
    "fetch_latest_account_article_source",
    "fetch_setting",
    "fetch_settings",
    "find_account",
    "import_config_bundle",
    "insert_account",
    "record_account_name_failure",
    "record_account_name_success",
    "update_account",
    "update_setting",
]
