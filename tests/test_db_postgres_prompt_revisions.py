from __future__ import annotations

from typing import Any, Iterator, Optional

import psycopg
import pytest
from psycopg.rows import dict_row

from src.adapters.db_postgres_prompt_revisions import (
    PromptRevisionConflictError,
    append_prompt_revision,
    fetch_latest_prompt_revisions,
    fetch_prompt_revision,
    fetch_prompt_revisions,
)
from src.config import get_settings

ACTOR_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
def conn() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    try:
        connection = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            connect_timeout=5,
        )
    except psycopg.OperationalError as exc:  # pragma: no cover - local without DB
        pytest.skip(f"Postgres unavailable: {exc}")
    with connection.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE console_users "
            "(LIKE public.console_users INCLUDING ALL) ON COMMIT DROP"
        )
        cur.execute(
            "CREATE TEMP TABLE prompt_revisions "
            "(LIKE public.prompt_revisions INCLUDING ALL) ON COMMIT DROP"
        )
        cur.execute(
            """
            INSERT INTO console_users (id, username, display_name, password_hash, role)
            VALUES (%s, 'admin', '编辑甲', 'hash', 'admin')
            """,
            (ACTOR_ID,),
        )
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _append(
    cur: psycopg.Cursor,
    *,
    content: str,
    expected_revision_id: Any,
    baseline: Any = None,
    note: Optional[str] = None,
) -> dict[str, Any]:
    return append_prompt_revision(
        cur,
        prompt_key="external_positive",
        content=content,
        note=note,
        expected_revision_id=expected_revision_id,
        baseline=baseline,
        actor_user_id=ACTOR_ID,
    )


def test_first_save_seeds_file_baseline_then_appends_new_revision(conn) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        row = _append(
            cur,
            content="new body",
            expected_revision_id=None,
            baseline={"revision": 2, "content": "file body"},
            note="收紧口径",
        )
        assert row["revision"] == 3
        assert row["content"] == "new body"
        assert row["note"] == "收紧口径"
        assert row["created_by_display_name"] == "编辑甲"

        latest = fetch_latest_prompt_revisions(cur)
        assert latest["external_positive"]["revision"] == 3
        history = fetch_prompt_revisions(cur, "external_positive", limit=10)
        assert [item["revision"] for item in history] == [3, 2]
        assert history[1]["content"] == "file body"
        assert history[1]["note"] == "初始文件版本"
        assert history[1]["created_by_user_id"] is None


def test_save_without_file_baseline_starts_at_v1(conn) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        row = _append(cur, content="first body", expected_revision_id=None)
        assert row["revision"] == 1
        history = fetch_prompt_revisions(cur, "external_positive", limit=10)
        assert len(history) == 1


def test_stale_expected_revision_is_rejected(conn) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        _append(cur, content="first body", expected_revision_id=None)
        with pytest.raises(PromptRevisionConflictError):
            _append(cur, content="second body", expected_revision_id=None)


def test_expected_revision_id_must_match_latest(conn) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        first = _append(cur, content="first body", expected_revision_id=None)
        second = _append(
            cur,
            content="second body",
            expected_revision_id=int(first["id"]),
        )
        assert second["revision"] == 2
        with pytest.raises(PromptRevisionConflictError):
            _append(
                cur,
                content="third body",
                expected_revision_id=int(first["id"]),
            )


def test_fetch_revision_returns_content_and_author(conn) -> None:
    with conn.cursor(row_factory=dict_row) as cur:
        row = _append(cur, content="body", expected_revision_id=None)
        fetched = fetch_prompt_revision(cur, "external_positive", int(row["id"]))
        assert fetched is not None
        assert fetched["content"] == "body"
        assert fetched["created_by_display_name"] == "编辑甲"
        assert fetch_prompt_revision(cur, "external_positive", 999999) is None
