from __future__ import annotations

from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parent.parent
    / "database"
    / "migrations"
    / "20260902030306_add_submission_item_duplicate_matches.sql"
)


def test_submission_item_duplicate_migration_has_reversible_table() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    up, down = sql.split("-- migrate:down", maxsplit=1)

    assert "create table if not exists public.submission_item_duplicate_matches" in up
    assert "unique (\n        item_id, prior_item_id\n    )" in up
    assert "match_method in ('article', 'title_hash', 'vector')" in up
    assert "drop table if exists public.submission_item_duplicate_matches" in down
    assert " state " not in up.lower()
