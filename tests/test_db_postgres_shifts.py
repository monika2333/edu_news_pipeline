from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.adapters.db_postgres_shifts import fetch_overlapping_duty_shift


class FakeCursor:
    def __init__(self) -> None:
        self.query = ""
        self.params: tuple[Any, ...] = ()

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params

    def fetchone(self) -> Optional[dict[str, Any]]:
        return None


def test_overlap_query_casts_optional_shift_id_to_uuid() -> None:
    cur: Any = FakeCursor()
    starts_at = datetime(2026, 1, 10, 22, tzinfo=timezone.utc)
    ends_at = datetime(2026, 1, 11, 22, tzinfo=timezone.utc)

    result = fetch_overlapping_duty_shift(
        cur,
        starts_at=starts_at,
        ends_at=ends_at,
        exclude_shift_id=None,
    )

    assert result is None
    assert "(%s::uuid IS NULL OR s.id <> %s::uuid)" in cur.query
    assert cur.params == (ends_at, starts_at, None, None)
