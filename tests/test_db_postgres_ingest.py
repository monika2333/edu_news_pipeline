from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from src.adapters.db_postgres_ingest import (
    get_raw_articles_missing_content,
    get_seen_raw_tokens,
)
from src.config import get_settings


class _Cursor:
    def __init__(self) -> None:
        self.query = ""

    def execute(self, query: str) -> None:
        self.query = query

    @staticmethod
    def fetchall() -> list[dict[str, Any]]:
        return [
            {"token": "toutiao-token"},
            {"token": "tencent-token"},
            {"token": "toutiao-token"},
            {"token": None},
        ]


def test_get_seen_raw_tokens_returns_distinct_non_empty_values() -> None:
    cursor: Any = _Cursor()

    tokens = get_seen_raw_tokens(cursor)

    assert tokens == {"toutiao-token", "tencent-token"}
    assert "SELECT DISTINCT token FROM raw_articles" in cursor.query
    assert "token IS NOT NULL" in cursor.query
    assert "BTRIM(token) <> ''" in cursor.query


FETCHED_AT = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)


@pytest.fixture()
def raw_articles_conn() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    try:
        connection = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password,
            dbname=settings.db_name,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=5,
        )
    except psycopg.OperationalError as exc:  # pragma: no cover - local without DB
        pytest.skip(f"Postgres unavailable: {exc}")
    try:
        with connection.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE raw_articles "
                "(LIKE public.raw_articles INCLUDING ALL) ON COMMIT PRESERVE ROWS"
            )
            cur.executemany(
                "INSERT INTO raw_articles"
                " (article_id, content_markdown, detail_fetched_at)"
                " VALUES (%s, %s, %s)",
                [
                    ("never-fetched", None, None),
                    ("blank-never-fetched", "   ", None),
                    ("empty-fetched", "", FETCHED_AT),
                    ("blank-fetched", "   ", FETCHED_AT),
                    ("content-never-marked", "正文", None),
                    ("content-fetched", "正文", FETCHED_AT),
                ],
            )
        yield connection
    finally:
        connection.close()


def test_missing_content_retries_only_rows_never_fetched(
    raw_articles_conn: psycopg.Connection,
) -> None:
    """正文为空且没写过 detail_fetched_at 才算缺正文。

    抓取失败不会写 detail_fetched_at，下一轮要能重试；已经抓过但确实没有
    文字的稿（纯图片版面）写了 detail_fetched_at，不再反复抓。
    """

    with raw_articles_conn.cursor() as cur:
        missing = get_raw_articles_missing_content(
            cur,
            [
                "never-fetched",
                "blank-never-fetched",
                "empty-fetched",
                "blank-fetched",
                "content-never-marked",
                "content-fetched",
                "not-in-table",
            ],
        )

    assert missing == {"never-fetched", "blank-never-fetched"}


def test_missing_content_without_ids_skips_the_query(
    raw_articles_conn: psycopg.Connection,
) -> None:
    empty_ids: list[Any] = ["", None]
    with raw_articles_conn.cursor() as cur:
        assert get_raw_articles_missing_content(cur, []) == set()
        assert get_raw_articles_missing_content(cur, empty_ids) == set()
