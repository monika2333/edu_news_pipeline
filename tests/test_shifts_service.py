from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Optional, Sequence

import pytest

from src.console import shifts_service
from src.console.auth_service import ConsoleUser


class FakeUsersNamespace:
    def __init__(self, adapter: FakeShiftAdapter) -> None:
        self._adapter = adapter

    def fetch_by_id(self, user_id: str) -> Optional[dict[str, Any]]:
        return self._adapter.users_by_id.get(user_id)


class FakeShiftAdapter:
    def __init__(self, schedule: Optional[list[dict[str, Any]]] = None) -> None:
        self.schedule = schedule or []
        self.inserted_rows: list[dict[str, Any]] = []
        self.users_by_id: dict[str, dict[str, Any]] = {
            str(item["user_id"]): {
                "id": str(item["user_id"]),
                "role": "duty_editor",
                "is_active": True,
            }
            for item in self.schedule
        }
        self.users = FakeUsersNamespace(self)

    def fetch_duty_schedule(self) -> list[dict[str, Any]]:
        return list(self.schedule)

    def insert_duty_shifts(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> int:
        self.inserted_rows = [dict(row) for row in rows]
        return len(self.inserted_rows)

    def upsert_duty_schedule(
        self,
        assignments: Mapping[int, str],
        *,
        actor_user_id: Optional[str],
    ) -> list[dict[str, Any]]:
        del actor_user_id
        self.schedule = [
            {"weekday": weekday, "user_id": user_id}
            for weekday, user_id in sorted(assignments.items())
        ]
        return list(self.schedule)


def _complete_schedule() -> list[dict[str, Any]]:
    return [
        {"weekday": weekday, "user_id": f"editor-{weekday}"}
        for weekday in range(7)
    ]


def test_generate_shifts_uses_coverage_date_weekday_and_boundary(monkeypatch) -> None:
    adapter = FakeShiftAdapter(_complete_schedule())
    monkeypatch.setattr(shifts_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        shifts_service,
        "get_settings",
        lambda: SimpleNamespace(duty_shift_boundary_hour=22),
    )
    now = datetime(2026, 7, 24, 13, 0, tzinfo=timezone.utc)  # 21:00 in Shanghai

    result = shifts_service.generate_shifts(days=2, now=now)

    first, second = adapter.inserted_rows
    assert first["ends_at"].isoformat() == "2026-07-24T22:00:00+08:00"
    assert first["starts_at"].isoformat() == "2026-07-23T22:00:00+08:00"
    assert first["user_id"] == "editor-4"  # Friday, based on coverage date
    assert second["user_id"] == "editor-5"
    assert result["inserted"] == 2


def test_generate_shifts_after_boundary_starts_with_next_coverage_date(
    monkeypatch,
) -> None:
    adapter = FakeShiftAdapter(_complete_schedule())
    monkeypatch.setattr(shifts_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        shifts_service,
        "get_settings",
        lambda: SimpleNamespace(duty_shift_boundary_hour=22),
    )
    now = datetime(2026, 7, 24, 15, 0, tzinfo=timezone.utc)  # 23:00 in Shanghai

    shifts_service.generate_shifts(days=1, now=now)

    assert adapter.inserted_rows[0]["ends_at"].isoformat() == (
        "2026-07-25T22:00:00+08:00"
    )


def test_generate_shifts_rejects_incomplete_template(monkeypatch) -> None:
    adapter = FakeShiftAdapter(_complete_schedule()[:-1])
    monkeypatch.setattr(shifts_service, "get_adapter", lambda: adapter)

    with pytest.raises(shifts_service.ShiftScheduleIncompleteError):
        shifts_service.generate_shifts(days=14)

    assert adapter.inserted_rows == []


def test_generate_shifts_rejects_inactive_template_editor(monkeypatch) -> None:
    adapter = FakeShiftAdapter(_complete_schedule())
    adapter.users_by_id["editor-3"]["is_active"] = False
    monkeypatch.setattr(shifts_service, "get_adapter", lambda: adapter)

    with pytest.raises(
        shifts_service.ShiftScheduleIncompleteError,
        match="inactive or invalid editors",
    ):
        shifts_service.generate_shifts(days=14)

    assert adapter.inserted_rows == []


def test_set_schedule_accepts_only_active_duty_editors(monkeypatch) -> None:
    adapter = FakeShiftAdapter()
    adapter.users_by_id = {
        f"editor-{weekday}": {
            "id": f"editor-{weekday}",
            "role": "duty_editor",
            "is_active": True,
        }
        for weekday in range(7)
    }
    monkeypatch.setattr(shifts_service, "get_adapter", lambda: adapter)
    admin = ConsoleUser(
        method="session",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )

    result = shifts_service.set_schedule(
        {weekday: f"editor-{weekday}" for weekday in range(7)},
        actor=admin,
    )

    assert len(result) == 7


def test_shift_migration_preserves_manual_reassignments() -> None:
    migration = (
        shifts_service.__file__
        and (
            Path(shifts_service.__file__).parents[2]
            / "database"
            / "migrations"
            / "20260724200000_add_duty_shifts_and_reviews.sql"
        ).read_text(encoding="utf-8")
    )

    assert "ON CONFLICT" not in migration
    adapter_source = (
        Path(shifts_service.__file__).parents[1]
        / "adapters"
        / "db_postgres_shifts.py"
    ).read_text(encoding="utf-8")
    assert "ON CONFLICT (starts_at) DO NOTHING" in adapter_source
    assert "DO UPDATE" not in adapter_source.split(
        "def insert_duty_shifts", 1
    )[1].split("def create_duty_shift", 1)[0]
