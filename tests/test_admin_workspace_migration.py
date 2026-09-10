from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from src.config import get_settings


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "database"
    / "migrations"
    / "20260910120000_isolate_admin_workspaces.sql"
)
ADMIN_A = "00000000-0000-0000-0000-000000000101"
ADMIN_B = "00000000-0000-0000-0000-000000000102"
ADMIN_C = "00000000-0000-0000-0000-000000000103"
ADMIN_D = "00000000-0000-0000-0000-000000000104"
EDITOR_E = "00000000-0000-0000-0000-000000000105"


@contextmanager
def _isolated_database() -> Iterator[psycopg.Connection]:
    settings = get_settings()
    database_name = f"test_admin_workspace_{uuid4().hex}"
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


def _create_legacy_schema(connection: psycopg.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE public.console_users (
            id uuid PRIMARY KEY,
            role text NOT NULL,
            is_active boolean NOT NULL,
            deleted_at timestamptz
        );

        CREATE TABLE public.manual_reviews (
            id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
            article_id text NOT NULL,
            status text NOT NULL,
            summary text,
            rank double precision,
            notes text,
            score numeric(6,3),
            decided_by text,
            decided_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            manual_llm_source text,
            report_type text,
            decided_by_user_id uuid,
            version integer DEFAULT 1 NOT NULL,
            CONSTRAINT manual_reviews_article_id_key UNIQUE (article_id)
        );
        CREATE INDEX manual_reviews_pending_idx
            ON public.manual_reviews (coalesce(report_type, 'zongbao'), rank, article_id)
            WHERE status = 'pending';
        CREATE INDEX manual_reviews_status_idx
            ON public.manual_reviews (status, coalesce(report_type, 'zongbao'));
        CREATE INDEX manual_reviews_status_report_type_rank_idx
            ON public.manual_reviews (
                status, coalesce(report_type, 'zongbao'), rank, article_id
            );

        CREATE TABLE public.shift_reviews (
            id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
            shift_id uuid NOT NULL,
            admin_discarded_at timestamptz,
            admin_discarded_by_user_id uuid,
            CONSTRAINT shift_reviews_admin_discarded_by_user_id_fkey
                FOREIGN KEY (admin_discarded_by_user_id)
                REFERENCES public.console_users(id) ON DELETE RESTRICT
        );
        CREATE INDEX shift_reviews_admin_discarded_idx
            ON public.shift_reviews (shift_id, admin_discarded_at DESC)
            WHERE admin_discarded_at IS NOT NULL;
        """
    )


def _seed_users(connection: psycopg.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO public.console_users (id, role, is_active, deleted_at)
            VALUES (%s, %s, %s, %s)
            """,
            [
                (ADMIN_A, "admin", True, None),
                (ADMIN_B, "admin", True, None),
                (ADMIN_C, "admin", False, None),
                (
                    ADMIN_D,
                    "admin",
                    True,
                    datetime(2026, 9, 1, tzinfo=timezone.utc),
                ),
                (EDITOR_E, "duty_editor", True, None),
            ],
        )


def test_migration_fans_out_legacy_rows_and_discards_then_down_keeps_latest() -> None:
    up_sql, down_sql = _migration_parts()
    discarded_at = datetime(2026, 9, 2, 8, tzinfo=timezone.utc)
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        _seed_users(connection)
        connection.execute(
            """
            INSERT INTO public.manual_reviews (
                article_id, status, summary, rank, notes, score, decided_by,
                decided_at, manual_llm_source, report_type, version
            )
            VALUES (
                'legacy-article', 'selected', '保留摘要', 3, '保留备注', 88,
                'legacy-admin', %s, '保留来源', 'wanbao', 7
            )
            """,
            (discarded_at,),
        )
        shift_review_id = connection.execute(
            """
            INSERT INTO public.shift_reviews (
                shift_id, admin_discarded_at, admin_discarded_by_user_id
            )
            VALUES (gen_random_uuid(), %s, %s)
            RETURNING id
            """,
            (discarded_at, EDITOR_E),
        ).fetchone()["id"]

        connection.execute(up_sql)

        manual_rows = connection.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, status, summary, rank,
                   notes, score::float8 AS score, manual_llm_source, report_type,
                   version
            FROM public.manual_reviews
            ORDER BY owner_user_id
            """
        ).fetchall()
        discard_rows = connection.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, discarded_at,
                   discarded_by_user_id::text AS discarded_by_user_id
            FROM public.shift_review_admin_discards
            ORDER BY owner_user_id
            """
        ).fetchall()
        active_ids = connection.execute(
            """
            SELECT id::text AS id
            FROM public.active_console_admins
            ORDER BY id
            """
        ).fetchall()
        owner_column = connection.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'manual_reviews'
              AND column_name = 'owner_user_id'
            """
        ).fetchone()
        unique_definition = connection.execute(
            """
            SELECT pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid = 'public.manual_reviews'::regclass
              AND conname = 'manual_reviews_owner_article_unique'
            """
        ).fetchone()["definition"]
        manual_index_definitions = connection.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = ANY(%s)
            ORDER BY indexname
            """,
            (
                [
                    "manual_reviews_pending_idx",
                    "manual_reviews_status_idx",
                    "manual_reviews_status_report_type_rank_idx",
                ],
            ),
        ).fetchall()
        legacy_shift_columns = connection.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'shift_reviews'
              AND column_name IN (
                  'admin_discarded_at',
                  'admin_discarded_by_user_id'
              )
            """
        ).fetchall()

        assert [row["owner_user_id"] for row in manual_rows] == [ADMIN_A, ADMIN_B]
        assert all(
            {
                "status": row["status"],
                "summary": row["summary"],
                "rank": row["rank"],
                "notes": row["notes"],
                "score": row["score"],
                "manual_llm_source": row["manual_llm_source"],
                "report_type": row["report_type"],
                "version": row["version"],
            }
            == {
                "status": "selected",
                "summary": "保留摘要",
                "rank": 3.0,
                "notes": "保留备注",
                "score": 88.0,
                "manual_llm_source": "保留来源",
                "report_type": "wanbao",
                "version": 7,
            }
            for row in manual_rows
        )
        assert [row["owner_user_id"] for row in discard_rows] == [ADMIN_A, ADMIN_B]
        assert all(row["discarded_at"] == discarded_at for row in discard_rows)
        assert all(row["discarded_by_user_id"] == EDITOR_E for row in discard_rows)
        assert [row["id"] for row in active_ids] == [ADMIN_A, ADMIN_B]
        assert owner_column == {"is_nullable": "NO"}
        assert unique_definition == "UNIQUE (owner_user_id, article_id)"
        assert len(manual_index_definitions) == 3
        assert all(
            "(owner_user_id," in row["indexdef"]
            for row in manual_index_definitions
        )
        assert legacy_shift_columns == []

        connection.execute(
            """
            UPDATE public.manual_reviews
            SET summary = 'B 最新', updated_at = '2099-09-04T00:00:00Z'
            WHERE owner_user_id = %s
            """,
            (ADMIN_B,),
        )
        connection.execute(
            """
            UPDATE public.shift_review_admin_discards
            SET discarded_at = '2099-09-05T00:00:00Z'
            WHERE owner_user_id = %s
            """,
            (ADMIN_B,),
        )
        connection.execute(down_sql)

        restored_manual = connection.execute(
            """
            SELECT summary, version
            FROM public.manual_reviews
            WHERE article_id = 'legacy-article'
            """
        ).fetchone()
        restored_shift = connection.execute(
            """
            SELECT admin_discarded_at
            FROM public.shift_reviews
            WHERE id = %s
            """,
            (shift_review_id,),
        ).fetchone()
        assert restored_manual == {"summary": "B 最新", "version": 7}
        assert restored_shift["admin_discarded_at"] == datetime(
            2099, 9, 5, tzinfo=timezone.utc
        )


def test_migration_aborts_without_active_admin_and_preserves_legacy_data() -> None:
    up_sql, _ = _migration_parts()
    with _isolated_database() as connection:
        _create_legacy_schema(connection)
        connection.execute(
            """
            INSERT INTO public.manual_reviews (article_id, status, version)
            VALUES ('must-survive', 'pending', 9)
            """
        )

        with pytest.raises(
            psycopg.errors.RaiseException,
            match="without an active administrator",
        ):
            connection.execute(up_sql)

        row = connection.execute(
            """
            SELECT article_id, status, version
            FROM public.manual_reviews
            """
        ).fetchone()
        owner_column = connection.execute(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'manual_reviews'
              AND column_name = 'owner_user_id'
            """
        ).fetchone()
        assert row == {
            "article_id": "must-survive",
            "status": "pending",
            "version": 9,
        }
        assert owner_column is None
