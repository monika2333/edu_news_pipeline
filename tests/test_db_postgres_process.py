from __future__ import annotations

from typing import Any, Optional

from src.adapters import db_postgres_process


class FakeCursor:
    def __init__(self) -> None:
        self.query: Optional[str] = None
        self.params: Optional[list[Any]] = None
        self.rowcount = 1

    def execute(self, query: str, params: list[Any]) -> None:
        self.query = query
        self.params = params


def test_complete_external_filter_records_prompt_metadata() -> None:
    cur = FakeCursor()

    db_postgres_process.complete_external_filter(
        cur,
        "article-1",
        passed=True,
        score=82,
        raw_output="82",
        category="internal",
        prompt_key="internal_positive",
        prompt_version="v2",
    )

    assert cur.params is not None
    raw_payload = cur.params[4].obj
    assert raw_payload["category"] == "internal"
    assert raw_payload["prompt_key"] == "internal_positive"
    assert raw_payload["prompt_version"] == "v2"
