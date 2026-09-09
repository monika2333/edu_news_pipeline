from __future__ import annotations

import inspect
from datetime import date, datetime, timezone
from typing import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from src.adapters import db_postgres_manual_reviews
from src.adapters.db_postgres_core import PostgresAdapter
from src.config import get_settings


ACTIVE_DISCARDED = "active-discarded"
ACTIVE_PENDING = "active-pending"
CANCELLED = "cancelled"
MULTI_SHIFT = "multi-shift"
UNPROCESSED = "unprocessed"
ALL_ARTICLE_IDS = {
    ACTIVE_DISCARDED,
    ACTIVE_PENDING,
    CANCELLED,
    MULTI_SHIFT,
    UNPROCESSED,
}
UNPROCESSED_ARTICLE_IDS = {CANCELLED, UNPROCESSED}


@pytest.fixture
def duty_filter_adapter() -> Iterator[PostgresAdapter]:
    settings = get_settings()
    conn = psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=True,
        row_factory=dict_row,
    )
    try:
        with conn.cursor() as cur:
            for table in (
                "console_users",
                "duty_shifts",
                "shift_reviews",
                "news_summaries",
                "manual_reviews",
                "manual_clusters",
                "score_feedbacks",
                "review_events",
            ):
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"(LIKE public.{table} INCLUDING DEFAULTS) ON COMMIT PRESERVE ROWS"
                )
            cur.execute(
                "ALTER TABLE review_events ALTER COLUMN id SET DEFAULT -1"
            )
            _seed_duty_filter_rows(cur)
        yield PostgresAdapter(connection=conn)
    finally:
        conn.close()


def _seed_duty_filter_rows(cur: psycopg.Cursor) -> None:
    user_id = "00000000-0000-0000-0000-000000000001"
    active_shift_id = "00000000-0000-0000-0000-000000000011"
    second_shift_id = "00000000-0000-0000-0000-000000000012"
    cancelled_shift_id = "00000000-0000-0000-0000-000000000013"
    cur.executemany(
        """
        INSERT INTO duty_shifts (id, user_id, starts_at, ends_at, cancelled_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [
            (
                active_shift_id,
                user_id,
                datetime(2026, 9, 1, tzinfo=timezone.utc),
                datetime(2026, 9, 2, tzinfo=timezone.utc),
                None,
            ),
            (
                second_shift_id,
                user_id,
                datetime(2026, 9, 2, tzinfo=timezone.utc),
                datetime(2026, 9, 3, tzinfo=timezone.utc),
                None,
            ),
            (
                cancelled_shift_id,
                user_id,
                datetime(2026, 9, 3, tzinfo=timezone.utc),
                datetime(2026, 9, 4, tzinfo=timezone.utc),
                datetime(2026, 9, 3, 1, tzinfo=timezone.utc),
            ),
        ],
    )
    scores = {
        ACTIVE_DISCARDED: 99,
        ACTIVE_PENDING: 98,
        MULTI_SHIFT: 97,
        UNPROCESSED: 90,
        CANCELLED: 80,
    }
    cur.executemany(
        """
        INSERT INTO news_summaries (
            article_id,
            title,
            llm_summary,
            content_markdown,
            status,
            score,
            external_importance_score,
            sentiment_label,
            is_beijing_related,
            created_at,
            score_details
        )
        VALUES (%s, %s, %s, %s, 'ready_for_export', %s, %s, 'positive', true, %s, '{}'::jsonb)
        """,
        [
            (
                article_id,
                f"Needle {article_id}",
                f"summary {article_id}",
                f"body {article_id}",
                score,
                score,
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
            for article_id, score in scores.items()
        ],
    )
    cur.executemany(
        """
        INSERT INTO manual_reviews (article_id, status, version)
        VALUES (%s, 'pending', 1)
        """,
        [(article_id,) for article_id in scores],
    )
    cur.executemany(
        """
        INSERT INTO shift_reviews (
            shift_id,
            article_id,
            decision,
            created_by_user_id,
            updated_by_user_id
        )
        VALUES (%s, %s, %s, %s, %s)
        """,
        [
            (active_shift_id, ACTIVE_DISCARDED, "discarded", user_id, user_id),
            (active_shift_id, ACTIVE_PENDING, "pending", user_id, user_id),
            (cancelled_shift_id, CANCELLED, "selected", user_id, user_id),
            (active_shift_id, MULTI_SHIFT, "discarded", user_id, user_id),
            (second_shift_id, MULTI_SHIFT, "pending", user_id, user_id),
        ],
    )
    cur.executemany(
        """
        INSERT INTO manual_clusters (bucket_key, cluster_id, item_ids)
        VALUES (%s, %s, %s)
        """,
        [
            (
                "internal_positive",
                "fully-processed",
                [ACTIVE_PENDING, MULTI_SHIFT],
            ),
            (
                "internal_positive",
                "mixed",
                [ACTIVE_DISCARDED, UNPROCESSED, CANCELLED],
            ),
        ],
    )


def _ids(rows: list[dict[str, object]]) -> set[str]:
    return {str(row["article_id"]) for row in rows}


def test_duty_filter_excludes_any_active_shift_review_and_preserves_count(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    rows, total = duty_filter_adapter.manual_reviews.fetch(
        status="pending",
        limit=20,
        offset=0,
        only_ready=True,
        duty_unprocessed_only=True,
    )

    assert _ids(rows) == UNPROCESSED_ARTICLE_IDS
    assert total == 2


def test_duty_filter_default_false_preserves_the_complete_pending_pool(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    rows, total = duty_filter_adapter.manual_reviews.fetch(
        status="pending",
        limit=20,
        offset=0,
        only_ready=True,
    )

    assert _ids(rows) == ALL_ARTICLE_IDS
    assert total == 5


def test_duty_filter_applies_to_search_and_cluster_reads(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    search_rows, search_total = duty_filter_adapter.manual_reviews.search_candidates(
        query="Needle",
        limit=20,
        offset=0,
        duty_unprocessed_only=True,
    )
    cluster_rows = duty_filter_adapter.manual_reviews.fetch_clusters(
        bucket_key="internal_positive",
        duty_unprocessed_only=True,
    )

    assert _ids(search_rows) == UNPROCESSED_ARTICLE_IDS
    assert search_total == 2
    assert _ids(cluster_rows) == UNPROCESSED_ARTICLE_IDS
    assert {str(row["cluster_id"]) for row in cluster_rows} == {"mixed"}


def test_duty_filter_scopes_bulk_discard_count_and_locked_targets(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    matched = duty_filter_adapter.manual_reviews.count_candidates_before_date(
        region="internal",
        sentiment="positive",
        created_before=date(2026, 1, 1),
        duty_unprocessed_only=True,
    )
    with duty_filter_adapter.transaction() as cur:
        targets = db_postgres_manual_reviews.fetch_manual_candidates_before_date_for_update(
            cur,
            region="internal",
            sentiment="positive",
            created_before=date(2026, 1, 1),
            duty_unprocessed_only=True,
        )

    assert matched == 2
    assert _ids(targets) == UNPROCESSED_ARTICLE_IDS


def test_core_bulk_discard_updates_only_duty_unprocessed_targets(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    updated = duty_filter_adapter.discard_manual_candidates_before_date_as_user(
        region="internal",
        sentiment="positive",
        query=None,
        created_before=date(2026, 1, 1),
        report_type=None,
        actor_username="admin",
        actor_user_id=None,
        duty_unprocessed_only=True,
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            "SELECT article_id, status FROM manual_reviews ORDER BY article_id",
            (),
        )
        statuses = {
            str(row["article_id"]): str(row["status"])
            for row in cur.fetchall()
        }

    assert _ids(updated) == UNPROCESSED_ARTICLE_IDS
    assert statuses[UNPROCESSED] == "discarded"
    assert statuses[CANCELLED] == "discarded"
    assert statuses[ACTIVE_DISCARDED] == "pending"
    assert statuses[ACTIVE_PENDING] == "pending"
    assert statuses[MULTI_SHIFT] == "pending"


def test_namespace_bulk_discard_updates_only_duty_unprocessed_targets(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    updated = duty_filter_adapter.manual_reviews.bulk_discard_candidates(
        region="internal",
        sentiment="positive",
        created_before=date(2026, 1, 1),
        duty_unprocessed_only=True,
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            "SELECT article_id, status FROM manual_reviews ORDER BY article_id",
            (),
        )
        statuses = {
            str(row["article_id"]): str(row["status"])
            for row in cur.fetchall()
        }

    assert updated == 2
    assert statuses[UNPROCESSED] == "discarded"
    assert statuses[CANCELLED] == "discarded"
    assert statuses[ACTIVE_DISCARDED] == "pending"
    assert statuses[ACTIVE_PENDING] == "pending"
    assert statuses[MULTI_SHIFT] == "pending"


def test_cluster_refresh_input_remains_the_complete_pending_pool(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cluster_transaction() as cur:
        rows = db_postgres_manual_reviews.fetch_manual_pending_for_cluster(cur)

    assert _ids(rows) == ALL_ARTICLE_IDS
    assert "duty_unprocessed_only" not in inspect.signature(
        db_postgres_manual_reviews.fetch_manual_pending_for_cluster
    ).parameters
