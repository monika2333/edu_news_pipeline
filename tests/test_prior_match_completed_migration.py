from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "database"
    / "migrations"
    / "20260903120000_add_prior_match_completed_at.sql"
)


def test_prior_match_completed_migration_adds_and_backfills_column() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    up, down = sql.split("-- migrate:down")

    assert "add column if not exists prior_match_completed_at timestamptz" in up
    assert "set prior_match_completed_at = now()" in up
    assert "where prior_match_completed_at is null" in up
    assert "drop column if exists prior_match_completed_at" in down
