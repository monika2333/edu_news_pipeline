from __future__ import annotations

from typing import Any

from src.adapters.db_postgres_ingest import get_seen_raw_tokens


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
