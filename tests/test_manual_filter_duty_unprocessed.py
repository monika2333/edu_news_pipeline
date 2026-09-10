from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterator

import psycopg
import pytest
from psycopg.rows import dict_row

from src.adapters import db_postgres_manual_reviews, db_postgres_shift_reviews
from src.adapters.db_postgres_core import PostgresAdapter
from src.adapters.db_postgres_submission_archive._links import (
    fetch_link_candidate_bodies,
    fetch_pending_links,
)
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
OWNER_USER_ID = "00000000-0000-0000-0000-000000000101"
SECOND_OWNER_USER_ID = "00000000-0000-0000-0000-000000000102"
INACTIVE_ADMIN_ID = "00000000-0000-0000-0000-000000000103"
DELETED_ADMIN_ID = "00000000-0000-0000-0000-000000000104"
DUTY_EDITOR_ID = "00000000-0000-0000-0000-000000000001"
NEW_ADMIN_ID = "00000000-0000-0000-0000-000000000106"


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
                "brief_items",
                "submitted_reports",
                "submitted_report_items",
            ):
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"(LIKE public.{table} INCLUDING ALL) ON COMMIT PRESERVE ROWS"
                )
            cur.execute(
                "ALTER TABLE manual_reviews "
                "DROP CONSTRAINT IF EXISTS manual_reviews_article_id_key"
            )
            cur.execute(
                "ALTER TABLE manual_reviews "
                "ADD COLUMN IF NOT EXISTS owner_user_id uuid"
            )
            cur.execute(
                "CREATE UNIQUE INDEX duty_filter_manual_owner_article_idx "
                "ON manual_reviews (owner_user_id, article_id)"
            )
            cur.execute(
                """
                CREATE TEMP TABLE shift_review_admin_discards (
                    owner_user_id uuid NOT NULL,
                    shift_review_id uuid NOT NULL,
                    discarded_at timestamptz NOT NULL DEFAULT now(),
                    discarded_by_user_id uuid,
                    PRIMARY KEY (owner_user_id, shift_review_id)
                ) ON COMMIT PRESERVE ROWS
                """
            )
            cur.execute(
                """
                CREATE TEMP VIEW active_console_admins AS
                SELECT id
                FROM console_users
                WHERE role = 'admin'
                  AND is_active
                  AND deleted_at IS NULL
                """
            )
            cur.execute("CREATE TEMP SEQUENCE review_events_test_id_seq")
            cur.execute(
                "ALTER TABLE review_events ALTER COLUMN id SET DEFAULT "
                "nextval('review_events_test_id_seq')"
            )
            _seed_duty_filter_rows(cur)
        yield PostgresAdapter(connection=conn)
    finally:
        conn.close()


def _seed_duty_filter_rows(cur: psycopg.Cursor) -> None:
    user_id = DUTY_EDITOR_ID
    active_shift_id = "00000000-0000-0000-0000-000000000011"
    second_shift_id = "00000000-0000-0000-0000-000000000012"
    cancelled_shift_id = "00000000-0000-0000-0000-000000000013"
    cur.executemany(
        """
        INSERT INTO console_users (
            id,
            username,
            display_name,
            password_hash,
            role,
            is_active,
            deleted_at
        )
        VALUES (%s, %s, %s, 'hash', %s, %s, %s)
        """,
        [
            (OWNER_USER_ID, "admin-a", "管理员 A", "admin", True, None),
            (SECOND_OWNER_USER_ID, "admin-b", "管理员 B", "admin", True, None),
            (INACTIVE_ADMIN_ID, "admin-c", "管理员 C", "admin", False, None),
            (
                DELETED_ADMIN_ID,
                "admin-d",
                "管理员 D",
                "admin",
                True,
                datetime(2026, 8, 1, tzinfo=timezone.utc),
            ),
            (DUTY_EDITOR_ID, "editor-e", "值班编辑 E", "duty_editor", True, None),
        ],
    )
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
        INSERT INTO manual_reviews (owner_user_id, article_id, status, version)
        VALUES (%s, %s, 'pending', 1)
        """,
        [(OWNER_USER_ID, article_id) for article_id in scores],
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


def _insert_ready_article(
    cur: psycopg.Cursor,
    article_id: str,
    *,
    created_at: datetime,
    sentiment_label: str | None = "positive",
) -> None:
    cur.execute(
        """
        INSERT INTO news_summaries (
            article_id,
            title,
            llm_summary,
            content_markdown,
            status,
            score,
            external_importance_score,
            external_importance_checked_at,
            sentiment_label,
            is_beijing_related,
            created_at,
            score_details
        )
        VALUES (%s, %s, %s, %s, 'ready_for_export', 80, 80, now(), %s, true, %s, '{}'::jsonb)
        """,
        (
            article_id,
            f"title {article_id}",
            f"summary {article_id}",
            f"body {article_id}",
            sentiment_label,
            created_at,
        ),
    )


def test_enqueue_fans_out_only_to_active_admins_without_overwrite(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    article_id = "fanout"
    with duty_filter_adapter.transaction() as cur:
        _insert_ready_article(
            cur,
            article_id,
            created_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )
        db_postgres_manual_reviews.enqueue_manual_review(cur, article_id)
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = 'selected', summary = 'A 的决定', version = 7
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (OWNER_USER_ID, article_id),
        )
        db_postgres_manual_reviews.enqueue_manual_review(cur, article_id)
        cur.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, status, summary, version
            FROM manual_reviews
            WHERE article_id = %s
            ORDER BY owner_user_id
            """,
            (article_id,),
        )
        rows = cur.fetchall()
        cur.execute(
            """
            INSERT INTO console_users (
                id, username, display_name, password_hash, role, is_active
            )
            VALUES (%s, 'admin-new', '新管理员', 'hash', 'admin', true)
            """,
            (NEW_ADMIN_ID,),
        )
        _insert_ready_article(
            cur,
            "after-new-admin",
            created_at=datetime(2026, 9, 6, tzinfo=timezone.utc),
        )
        db_postgres_manual_reviews.enqueue_manual_review(cur, "after-new-admin")
        cur.execute(
            """
            SELECT article_id
            FROM manual_reviews
            WHERE owner_user_id = %s
            ORDER BY article_id
            """,
            (NEW_ADMIN_ID,),
        )
        new_admin_articles = cur.fetchall()
        cur.execute(
            "UPDATE console_users SET is_active = false WHERE role = 'admin'"
        )
        _insert_ready_article(
            cur,
            "zero-active-admins",
            created_at=datetime(2026, 9, 7, tzinfo=timezone.utc),
        )
        db_postgres_manual_reviews.enqueue_manual_review(
            cur,
            "zero-active-admins",
        )
        cur.execute(
            "SELECT count(*) AS total FROM manual_reviews WHERE article_id = %s",
            ("zero-active-admins",),
        )
        zero_admin_count = cur.fetchone()["total"]

    assert rows == [
        {
            "owner_user_id": OWNER_USER_ID,
            "status": "selected",
            "summary": "A 的决定",
            "version": 7,
        },
        {
            "owner_user_id": SECOND_OWNER_USER_ID,
            "status": "pending",
            "summary": None,
            "version": 1,
        },
    ]
    assert new_admin_articles == [{"article_id": "after-new-admin"}]
    assert zero_admin_count == 0


def test_decision_rank_and_versioned_write_are_owner_scoped(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, rank, summary, version
            )
            VALUES (%s, %s, 'selected', 10, 'B 的摘要', 1)
            """,
            (SECOND_OWNER_USER_ID, UNPROCESSED),
        )

    saved = duty_filter_adapter.update_manual_review_statuses_as_user(
        [
            {
                "article_id": UNPROCESSED,
                "status": "selected",
                "report_type": "zongbao",
            }
        ],
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        expected_versions={UNPROCESSED: 1},
        require_versions=True,
        action="manual_review.decide",
        report_type="zongbao",
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, status, rank, summary, version
            FROM manual_reviews
            WHERE article_id = %s
            ORDER BY owner_user_id
            """,
            (UNPROCESSED,),
        )
        rows = cur.fetchall()

    assert saved[0]["rank"] == 1
    assert rows == [
        {
            "owner_user_id": OWNER_USER_ID,
            "status": "selected",
            "rank": 1.0,
            "summary": None,
            "version": 2,
        },
        {
            "owner_user_id": SECOND_OWNER_USER_ID,
            "status": "selected",
            "rank": 10.0,
            "summary": "B 的摘要",
            "version": 1,
        },
    ]


def test_edit_order_and_archive_writes_preserve_other_owner_rows(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, rank, summary, version
            )
            SELECT %s, article_id, 'pending', 9, 'B 原文', 11
            FROM manual_reviews
            WHERE owner_user_id = %s
            """,
            (SECOND_OWNER_USER_ID, OWNER_USER_ID),
        )

    duty_filter_adapter.update_manual_review_summaries_as_user(
        {ACTIVE_PENDING: {"summary": "A 编辑", "notes": "A 备注"}},
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        expected_versions={ACTIVE_PENDING: 1},
        require_versions=True,
        report_type="zongbao",
    )
    duty_filter_adapter.update_manual_review_order_as_user(
        [
            {
                "article_id": ACTIVE_DISCARDED,
                "status": "selected",
                "rank": 2,
                "report_type": "zongbao",
            }
        ],
        [],
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        report_type="zongbao",
    )
    duty_filter_adapter.update_manual_review_statuses_as_user(
        [
            {
                "article_id": MULTI_SHIFT,
                "status": "exported",
                "rank": None,
            }
        ],
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        expected_versions={MULTI_SHIFT: 1},
        require_versions=True,
        action="manual_review.archive",
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, article_id, status,
                   rank, summary, notes, version
            FROM manual_reviews
            WHERE article_id = ANY(%s)
            ORDER BY owner_user_id, article_id
            """,
            ([ACTIVE_PENDING, ACTIVE_DISCARDED, MULTI_SHIFT],),
        )
        rows = cur.fetchall()

    by_owner_article = {
        (row["owner_user_id"], row["article_id"]): row
        for row in rows
    }
    assert by_owner_article[(OWNER_USER_ID, ACTIVE_PENDING)]["summary"] == "A 编辑"
    assert by_owner_article[(OWNER_USER_ID, ACTIVE_PENDING)]["notes"] == "A 备注"
    assert by_owner_article[(OWNER_USER_ID, ACTIVE_DISCARDED)]["status"] == "selected"
    assert by_owner_article[(OWNER_USER_ID, ACTIVE_DISCARDED)]["rank"] == 2
    assert by_owner_article[(OWNER_USER_ID, MULTI_SHIFT)]["status"] == "exported"
    assert all(
        by_owner_article[(SECOND_OWNER_USER_ID, article_id)]["status"] == "pending"
        and by_owner_article[(SECOND_OWNER_USER_ID, article_id)]["rank"] == 9
        and by_owner_article[(SECOND_OWNER_USER_ID, article_id)]["summary"] == "B 原文"
        and by_owner_article[(SECOND_OWNER_USER_ID, article_id)]["version"] == 11
        for article_id in (ACTIVE_PENDING, ACTIVE_DISCARDED, MULTI_SHIFT)
    )


def test_duty_filter_excludes_any_active_shift_review_and_preserves_count(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    rows, total = duty_filter_adapter.manual_reviews.fetch(
        owner_user_id=OWNER_USER_ID,
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
        owner_user_id=OWNER_USER_ID,
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
        owner_user_id=OWNER_USER_ID,
        query="Needle",
        limit=20,
        offset=0,
        duty_unprocessed_only=True,
    )
    cluster_rows = duty_filter_adapter.manual_reviews.fetch_clusters(
        owner_user_id=OWNER_USER_ID,
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
        owner_user_id=OWNER_USER_ID,
        region="internal",
        sentiment="positive",
        created_before=date(2026, 1, 1),
        duty_unprocessed_only=True,
    )
    with duty_filter_adapter.transaction() as cur:
        targets = db_postgres_manual_reviews.fetch_manual_candidates_before_date_for_update(
            cur,
            owner_user_id=OWNER_USER_ID,
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
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            INSERT INTO manual_reviews (owner_user_id, article_id, status, version)
            SELECT %s, article_id, status, version
            FROM manual_reviews
            WHERE owner_user_id = %s
            """,
            (SECOND_OWNER_USER_ID, OWNER_USER_ID),
        )
    updated = duty_filter_adapter.discard_manual_candidates_before_date_as_user(
        region="internal",
        sentiment="positive",
        query=None,
        created_before=date(2026, 1, 1),
        report_type="zongbao",
        actor_username="admin",
        actor_user_id=OWNER_USER_ID,
        duty_unprocessed_only=True,
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, article_id, status
            FROM manual_reviews
            ORDER BY owner_user_id, article_id
            """,
            (),
        )
        statuses = {
            (str(row["owner_user_id"]), str(row["article_id"])): str(row["status"])
            for row in cur.fetchall()
        }

    assert _ids(updated) == UNPROCESSED_ARTICLE_IDS
    assert statuses[(OWNER_USER_ID, UNPROCESSED)] == "discarded"
    assert statuses[(OWNER_USER_ID, CANCELLED)] == "discarded"
    assert statuses[(OWNER_USER_ID, ACTIVE_DISCARDED)] == "pending"
    assert statuses[(OWNER_USER_ID, ACTIVE_PENDING)] == "pending"
    assert statuses[(OWNER_USER_ID, MULTI_SHIFT)] == "pending"
    assert all(
        statuses[(SECOND_OWNER_USER_ID, article_id)] == "pending"
        for article_id in ALL_ARTICLE_IDS
    )


def test_cluster_refresh_input_ignores_manual_decisions(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cluster_transaction() as cur:
        rows = db_postgres_manual_reviews.fetch_manual_cluster_sources(cur)

    assert _ids(rows) == ALL_ARTICLE_IDS


def test_cluster_refresh_uses_latest_ready_limit_even_after_all_admins_decide(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cursor() as cur:
        cur.execute("UPDATE manual_reviews SET status = 'selected'")
        cur.execute(
            """
            UPDATE news_summaries
            SET created_at = CASE article_id
                WHEN %s THEN %s
                WHEN %s THEN %s
                ELSE %s
            END
            """,
            (
                UNPROCESSED,
                datetime(2026, 9, 5, tzinfo=timezone.utc),
                CANCELLED,
                datetime(2026, 9, 4, tzinfo=timezone.utc),
                datetime(2026, 9, 1, tzinfo=timezone.utc),
            ),
        )
        rows = db_postgres_manual_reviews.fetch_manual_cluster_sources(
            cur,
            fetch_limit=2,
        )

    assert [str(row["article_id"]) for row in rows] == [UNPROCESSED, CANCELLED]


def test_cluster_singleton_uses_owner_pending_and_null_sentiment_positive_bucket(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    article_id = "outside-cache"
    with duty_filter_adapter._cursor() as cur:
        _insert_ready_article(
            cur,
            article_id,
            created_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
            sentiment_label=None,
        )
        cur.executemany(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, report_type, version
            )
            VALUES (%s, %s, %s, 'zongbao', 1)
            """,
            [
                (OWNER_USER_ID, article_id, "pending"),
                (SECOND_OWNER_USER_ID, article_id, "selected"),
            ],
        )

    a_rows = duty_filter_adapter.manual_reviews.fetch_clusters(
        owner_user_id=OWNER_USER_ID,
        bucket_key="internal_positive",
    )
    b_rows = duty_filter_adapter.manual_reviews.fetch_clusters(
        owner_user_id=SECOND_OWNER_USER_ID,
        bucket_key="internal_positive",
    )

    a_singletons = [row for row in a_rows if row["article_id"] == article_id]
    b_singletons = [row for row in b_rows if row["article_id"] == article_id]
    assert len(a_singletons) == 1
    assert a_singletons[0]["cluster_id"] == f"single-{article_id}"
    assert a_singletons[0]["bucket_key"] == "internal_positive"
    assert b_singletons == []


def test_owner_clear_and_explicit_system_clear_have_distinct_scopes(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = 'selected', rank = 1
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (OWNER_USER_ID, UNPROCESSED),
        )
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, rank, version
            )
            VALUES (%s, %s, 'backup', 1, 1)
            """,
            (SECOND_OWNER_USER_ID, UNPROCESSED),
        )

    duty_filter_adapter.clear_review_buckets_for_owner_as_user(
        owner_user_id=OWNER_USER_ID,
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        trigger="manual",
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, status
            FROM manual_reviews
            WHERE article_id = %s
            ORDER BY owner_user_id
            """,
            (UNPROCESSED,),
        )
        after_owner_clear = cur.fetchall()

    duty_filter_adapter.clear_all_review_buckets_as_system(
        actor_username="system:scheduled_clear",
        trigger="scheduled",
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, status
            FROM manual_reviews
            WHERE article_id = %s
            ORDER BY owner_user_id
            """,
            (UNPROCESSED,),
        )
        after_system_clear = cur.fetchall()

    assert [row["status"] for row in after_owner_clear] == ["discarded", "backup"]
    assert [row["status"] for row in after_system_clear] == ["discarded", "discarded"]


def test_submission_link_body_collapses_admin_rows_by_archive_then_update(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    article_id = UNPROCESSED
    updated_fallback_article_id = ACTIVE_PENDING
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = 'selected', summary = 'A newer edit',
                decided_at = %s, updated_at = %s
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (
                datetime(2026, 9, 5, tzinfo=timezone.utc),
                datetime(2026, 9, 6, tzinfo=timezone.utc),
                OWNER_USER_ID,
                article_id,
            ),
        )
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary,
                decided_at, updated_at, version
            )
            VALUES (%s, %s, 'exported', 'B archived', %s, %s, 1)
            """,
            (
                SECOND_OWNER_USER_ID,
                article_id,
                datetime(2026, 9, 4, tzinfo=timezone.utc),
                datetime(2026, 9, 4, tzinfo=timezone.utc),
            ),
        )
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = 'selected', summary = 'A recent decision',
                decided_at = '2099-09-09T00:00:00Z',
                updated_at = '2026-09-01T00:00:00Z'
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (OWNER_USER_ID, updated_fallback_article_id),
        )
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary,
                decided_at, updated_at, version
            )
            VALUES (
                %s, %s, 'selected', 'B latest update',
                '2026-01-01T00:00:00Z', '2099-09-08T00:00:00Z', 1
            )
            """,
            (SECOND_OWNER_USER_ID, updated_fallback_article_id),
        )
        rows = fetch_link_candidate_bodies(
            cur,
            article_ids=[article_id, updated_fallback_article_id],
        )

    assert rows == [
        {"article_id": article_id, "body": "B archived"},
        {
            "article_id": updated_fallback_article_id,
            "body": "B latest update",
        },
    ]


def test_admin_summary_counts_and_discards_are_viewer_scoped(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    shift_id = "00000000-0000-0000-0000-000000000011"
    count_article = "summary-count"
    discard_article = "summary-discard"
    with duty_filter_adapter._cursor() as cur:
        for article_id in (count_article, discard_article):
            _insert_ready_article(
                cur,
                article_id,
                created_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            )
        cur.executemany(
            """
            INSERT INTO shift_reviews (
                shift_id, article_id, decision, report_type,
                created_by_user_id, updated_by_user_id
            )
            VALUES (%s, %s, 'selected', 'zongbao', %s, %s)
            """,
            [
                (shift_id, count_article, DUTY_EDITOR_ID, DUTY_EDITOR_ID),
                (shift_id, discard_article, DUTY_EDITOR_ID, DUTY_EDITOR_ID),
            ],
        )
        cur.executemany(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, report_type, version
            )
            VALUES (%s, %s, %s, 'zongbao', 1)
            """,
            [
                (OWNER_USER_ID, count_article, "pending"),
                (SECOND_OWNER_USER_ID, count_article, "selected"),
                (OWNER_USER_ID, discard_article, "pending"),
                (SECOND_OWNER_USER_ID, discard_article, "pending"),
            ],
        )
        a_summaries = db_postgres_shift_reviews.fetch_admin_shift_summaries(
            cur,
            viewer_user_id=OWNER_USER_ID,
        )
        b_summaries = db_postgres_shift_reviews.fetch_admin_shift_summaries(
            cur,
            viewer_user_id=SECOND_OWNER_USER_ID,
        )
        db_postgres_shift_reviews.set_admin_discarded(
            cur,
            shift_id=shift_id,
            article_id=discard_article,
            actor_user_id=OWNER_USER_ID,
            discarded=True,
        )
        a_rows, _ = db_postgres_shift_reviews.fetch_shift_review_items(
            cur,
            shift_id=shift_id,
            viewer_user_id=OWNER_USER_ID,
            decision="selected",
            report_type="zongbao",
            limit=200,
            offset=0,
            include_admin_state=True,
            admin_unprocessed_only=True,
        )
        b_rows, _ = db_postgres_shift_reviews.fetch_shift_review_items(
            cur,
            shift_id=shift_id,
            viewer_user_id=SECOND_OWNER_USER_ID,
            decision="selected",
            report_type="zongbao",
            limit=200,
            offset=0,
            include_admin_state=True,
            admin_unprocessed_only=True,
        )

    a_summary = next(row for row in a_summaries if str(row["shift_id"]) == shift_id)
    b_summary = next(row for row in b_summaries if str(row["shift_id"]) == shift_id)
    assert a_summary["zongbao_selected_all"] == 2
    assert b_summary["zongbao_selected_all"] == 2
    assert a_summary["zongbao_selected"] == 2
    assert b_summary["zongbao_selected"] == 1
    assert _ids(a_rows) == {count_article}
    assert _ids(b_rows) == {discard_article}


def test_import_and_conflict_preview_ignore_other_admin_rows(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    shift_id = "00000000-0000-0000-0000-000000000011"
    article_id = "import-owner-scope"
    with duty_filter_adapter._cursor() as cur:
        _insert_ready_article(
            cur,
            article_id,
            created_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        )
        cur.execute(
            """
            INSERT INTO shift_reviews (
                shift_id, article_id, decision, report_type, edited_summary,
                created_by_user_id, updated_by_user_id
            )
            VALUES (%s, %s, 'selected', 'zongbao', '值班摘要', %s, %s)
            """,
            (shift_id, article_id, DUTY_EDITOR_ID, DUTY_EDITOR_ID),
        )
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary, report_type, version
            )
            VALUES (%s, %s, 'selected', 'B 的摘要', 'zongbao', 6)
            """,
            (SECOND_OWNER_USER_ID, article_id),
        )
        preview = db_postgres_manual_reviews.preview_shift_reviews_for_manual(
            cur,
            owner_user_id=OWNER_USER_ID,
            shift_id=shift_id,
            article_ids=[article_id],
        )

    assert preview[0]["existing_id"] is None
    imported = duty_filter_adapter.import_shift_reviews_into_manual(
        shift_id=shift_id,
        article_ids=[article_id],
        target_status="selected",
        report_type="zongbao",
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        conflict_resolutions=[],
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT owner_user_id::text AS owner_user_id, status, summary, version
            FROM manual_reviews
            WHERE article_id = %s
            ORDER BY owner_user_id
            """,
            (article_id,),
        )
        rows = cur.fetchall()

    assert imported[0]["summary"] == "值班摘要"
    assert rows == [
        {
            "owner_user_id": OWNER_USER_ID,
            "status": "selected",
            "summary": "值班摘要",
            "version": 1,
        },
        {
            "owner_user_id": SECOND_OWNER_USER_ID,
            "status": "selected",
            "summary": "B 的摘要",
            "version": 6,
        },
    ]


def test_status_counts_are_owner_scoped_with_different_workspaces(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cursor() as cur:
        cur.execute("DELETE FROM manual_reviews")
        cur.executemany(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, report_type, version
            )
            VALUES (%s, %s, %s, 'zongbao', 1)
            """,
            [
                (OWNER_USER_ID, "count-a-pending-1", "pending"),
                (OWNER_USER_ID, "count-a-pending-2", "pending"),
                (OWNER_USER_ID, "count-a-selected", "selected"),
                (OWNER_USER_ID, "count-a-discarded", "discarded"),
                (SECOND_OWNER_USER_ID, "count-b-pending", "pending"),
                (SECOND_OWNER_USER_ID, "count-b-backup-1", "backup"),
                (SECOND_OWNER_USER_ID, "count-b-backup-2", "backup"),
                (SECOND_OWNER_USER_ID, "count-b-exported", "exported"),
            ],
        )

    a_counts = duty_filter_adapter.manual_reviews.status_counts(
        owner_user_id=OWNER_USER_ID,
        report_type="zongbao",
    )
    b_counts = duty_filter_adapter.manual_reviews.status_counts(
        owner_user_id=SECOND_OWNER_USER_ID,
        report_type="zongbao",
    )

    assert a_counts == {
        "pending": 2,
        "selected": 1,
        "backup": 0,
        "discarded": 1,
        "exported": 0,
    }
    assert b_counts == {
        "pending": 1,
        "selected": 0,
        "backup": 2,
        "discarded": 0,
        "exported": 1,
    }


def test_versioned_summary_edit_preserves_identical_other_owner_row(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary, notes, version
            )
            SELECT %s, article_id, status, summary, notes, version
            FROM manual_reviews
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (SECOND_OWNER_USER_ID, OWNER_USER_ID, ACTIVE_PENDING),
        )

    duty_filter_adapter.update_manual_review_summaries_as_user(
        {ACTIVE_PENDING: {"summary": "A 编辑后摘要", "notes": "A 编辑后备注"}},
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        expected_versions={ACTIVE_PENDING: 1},
        require_versions=True,
        report_type="zongbao",
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT summary, notes, version
            FROM manual_reviews
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (SECOND_OWNER_USER_ID, ACTIVE_PENDING),
        )
        b_row = cur.fetchone()

    assert b_row == {"summary": None, "notes": None, "version": 1}


def test_import_keep_existing_update_preserves_identical_other_owner_row(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    shift_id = "00000000-0000-0000-0000-000000000011"
    article_id = "import-keep-existing-owner-scope"
    with duty_filter_adapter._cursor() as cur:
        _insert_ready_article(
            cur,
            article_id,
            created_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        )
        cur.execute(
            """
            INSERT INTO shift_reviews (
                shift_id, article_id, decision, report_type, edited_summary,
                manual_llm_source, created_by_user_id, updated_by_user_id
            )
            VALUES (%s, %s, 'selected', 'zongbao', '值班摘要',
                    '值班来源', %s, %s)
            """,
            (shift_id, article_id, DUTY_EDITOR_ID, DUTY_EDITOR_ID),
        )
        cur.executemany(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary,
                manual_llm_source, report_type, version
            )
            VALUES (%s, %s, 'selected', '共同原摘要',
                    '共同原来源', 'zongbao', 3)
            """,
            [
                (OWNER_USER_ID, article_id),
                (SECOND_OWNER_USER_ID, article_id),
            ],
        )

    duty_filter_adapter.import_shift_reviews_into_manual(
        shift_id=shift_id,
        article_ids=[article_id],
        target_status="selected",
        report_type="zongbao",
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        conflict_resolutions=[
            {
                "article_id": article_id,
                "choice": "existing",
                "summary": "A 保留并编辑的摘要",
                "manual_llm_source": "A 保留并编辑的来源",
                "existing_version": 3,
            }
        ],
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT status, summary, manual_llm_source, version
            FROM manual_reviews
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (SECOND_OWNER_USER_ID, article_id),
        )
        b_row = cur.fetchone()

    assert b_row == {
        "status": "selected",
        "summary": "共同原摘要",
        "manual_llm_source": "共同原来源",
        "version": 3,
    }


def test_normal_import_update_preserves_identical_other_owner_row(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    shift_id = "00000000-0000-0000-0000-000000000011"
    article_id = "import-normal-update-owner-scope"
    with duty_filter_adapter._cursor() as cur:
        _insert_ready_article(
            cur,
            article_id,
            created_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        )
        cur.execute(
            """
            INSERT INTO shift_reviews (
                shift_id, article_id, decision, report_type, edited_summary,
                created_by_user_id, updated_by_user_id
            )
            VALUES (%s, %s, 'selected', 'zongbao', '值班导入摘要', %s, %s)
            """,
            (shift_id, article_id, DUTY_EDITOR_ID, DUTY_EDITOR_ID),
        )
        cur.executemany(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary, report_type, version
            )
            VALUES (%s, %s, 'pending', '共同待处理摘要', 'zongbao', 1)
            """,
            [
                (OWNER_USER_ID, article_id),
                (SECOND_OWNER_USER_ID, article_id),
            ],
        )

    duty_filter_adapter.import_shift_reviews_into_manual(
        shift_id=shift_id,
        article_ids=[article_id],
        target_status="selected",
        report_type="zongbao",
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        conflict_resolutions=[],
    )
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            SELECT status, summary, version
            FROM manual_reviews
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (SECOND_OWNER_USER_ID, article_id),
        )
        b_row = cur.fetchone()

    assert b_row == {
        "status": "pending",
        "summary": "共同待处理摘要",
        "version": 1,
    }


def test_admin_shift_summary_discards_are_viewer_scoped_without_row_multiplication(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    shift_id = "00000000-0000-0000-0000-000000000011"
    article_ids = ["summary-viewer-scope-1", "summary-viewer-scope-2"]
    with duty_filter_adapter._cursor() as cur:
        for article_id in article_ids:
            _insert_ready_article(
                cur,
                article_id,
                created_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            )
        cur.executemany(
            """
            INSERT INTO shift_reviews (
                shift_id, article_id, decision, report_type,
                created_by_user_id, updated_by_user_id
            )
            VALUES (%s, %s, 'selected', 'zongbao', %s, %s)
            """,
            [
                (shift_id, article_id, DUTY_EDITOR_ID, DUTY_EDITOR_ID)
                for article_id in article_ids
            ],
        )
        cur.executemany(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, report_type, version
            )
            VALUES (%s, %s, 'pending', 'zongbao', 1)
            """,
            [
                (owner_user_id, article_id)
                for owner_user_id in (OWNER_USER_ID, SECOND_OWNER_USER_ID)
                for article_id in article_ids
            ],
        )
        db_postgres_shift_reviews.set_admin_discarded(
            cur,
            shift_id=shift_id,
            article_id=article_ids[0],
            actor_user_id=OWNER_USER_ID,
            discarded=True,
        )
        after_a_discard = {
            owner_user_id: next(
                row
                for row in db_postgres_shift_reviews.fetch_admin_shift_summaries(
                    cur,
                    viewer_user_id=owner_user_id,
                )
                if str(row["shift_id"]) == shift_id
            )
            for owner_user_id in (OWNER_USER_ID, SECOND_OWNER_USER_ID)
        }
        db_postgres_shift_reviews.set_admin_discarded(
            cur,
            shift_id=shift_id,
            article_id=article_ids[0],
            actor_user_id=SECOND_OWNER_USER_ID,
            discarded=True,
        )
        after_b_discard = {
            owner_user_id: next(
                row
                for row in db_postgres_shift_reviews.fetch_admin_shift_summaries(
                    cur,
                    viewer_user_id=owner_user_id,
                )
                if str(row["shift_id"]) == shift_id
            )
            for owner_user_id in (OWNER_USER_ID, SECOND_OWNER_USER_ID)
        }

    assert after_a_discard[OWNER_USER_ID]["zongbao_selected"] == 1
    assert after_a_discard[SECOND_OWNER_USER_ID]["zongbao_selected"] == 2
    assert after_b_discard[OWNER_USER_ID]["zongbao_selected"] == 1
    assert after_b_discard[SECOND_OWNER_USER_ID]["zongbao_selected"] == 1
    assert after_b_discard[OWNER_USER_ID]["zongbao_selected_all"] == 2
    assert after_b_discard[SECOND_OWNER_USER_ID]["zongbao_selected_all"] == 2


def test_pending_links_collapse_owner_rows_by_archive_then_update(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    report_id = "00000000-0000-0000-0000-000000000901"
    archived_article_id = UNPROCESSED
    updated_article_id = ACTIVE_PENDING
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = 'selected', summary = 'A newer unarchived',
                decided_at = '2026-09-05T00:00:00Z',
                updated_at = '2026-09-06T00:00:00Z'
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (OWNER_USER_ID, archived_article_id),
        )
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary,
                decided_at, updated_at, version
            )
            VALUES (%s, %s, 'exported', 'B archived',
                    '2026-09-04T00:00:00Z', '2026-09-04T00:00:00Z', 1)
            """,
            (SECOND_OWNER_USER_ID, archived_article_id),
        )
        cur.execute(
            """
            UPDATE manual_reviews
            SET status = 'selected', summary = 'A older update',
                updated_at = '2026-09-01T00:00:00Z'
            WHERE owner_user_id = %s AND article_id = %s
            """,
            (OWNER_USER_ID, updated_article_id),
        )
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, summary, updated_at, version
            )
            VALUES (%s, %s, 'selected', 'B latest update',
                    '2026-09-08T00:00:00Z', 1)
            """,
            (SECOND_OWNER_USER_ID, updated_article_id),
        )
        cur.execute(
            """
            INSERT INTO submitted_reports (
                id, report_type, report_date, compiled_date, pasted_text
            )
            VALUES (%s, 'zongbao', '2026-09-09', '2026-09-09', 'test')
            """,
            (report_id,),
        )
        cur.executemany(
            """
            INSERT INTO submitted_report_items (
                report_id, order_index, title, body, norm_title,
                norm_title_hash, link_status, best_candidate_article_id
            )
            VALUES (%s, %s, %s, '', %s, %s, 'pending', %s)
            """,
            [
                (
                    report_id,
                    1,
                    "archived priority",
                    "archived priority",
                    "pending-link-1",
                    archived_article_id,
                ),
                (
                    report_id,
                    2,
                    "latest update",
                    "latest update",
                    "pending-link-2",
                    updated_article_id,
                ),
            ],
        )
        rows, total = fetch_pending_links(cur, limit=20, offset=0)

    assert total == 2
    assert len(rows) == 2
    assert [row["candidate_body"] for row in rows] == [
        "B archived",
        "B latest update",
    ]


def test_other_owner_reads_do_not_change_after_first_owner_decisions(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    with duty_filter_adapter._cursor() as cur:
        cur.execute(
            """
            INSERT INTO manual_reviews (
                owner_user_id, article_id, status, report_type, version
            )
            SELECT %s, article_id, status, report_type, version
            FROM manual_reviews
            WHERE owner_user_id = %s
            """,
            (SECOND_OWNER_USER_ID, OWNER_USER_ID),
        )

    before_pending = duty_filter_adapter.manual_reviews.fetch(
        owner_user_id=SECOND_OWNER_USER_ID,
        status="pending",
        limit=20,
        offset=0,
        only_ready=True,
    )
    before_selected = duty_filter_adapter.manual_reviews.fetch(
        owner_user_id=SECOND_OWNER_USER_ID,
        status="selected",
        limit=20,
        offset=0,
        only_ready=True,
        report_type="zongbao",
    )
    before_search = duty_filter_adapter.manual_reviews.search_candidates(
        owner_user_id=SECOND_OWNER_USER_ID,
        query="Needle",
        limit=20,
        offset=0,
    )
    duty_filter_adapter.update_manual_review_statuses_as_user(
        [
            {
                "article_id": ACTIVE_PENDING,
                "status": "selected",
                "report_type": "zongbao",
            },
            {
                "article_id": UNPROCESSED,
                "status": "discarded",
                "report_type": "zongbao",
            },
        ],
        actor_username="admin-a",
        actor_user_id=OWNER_USER_ID,
        expected_versions={ACTIVE_PENDING: 1, UNPROCESSED: 1},
        require_versions=True,
        action="manual_review.decide",
        report_type="zongbao",
    )
    after_pending = duty_filter_adapter.manual_reviews.fetch(
        owner_user_id=SECOND_OWNER_USER_ID,
        status="pending",
        limit=20,
        offset=0,
        only_ready=True,
    )
    after_selected = duty_filter_adapter.manual_reviews.fetch(
        owner_user_id=SECOND_OWNER_USER_ID,
        status="selected",
        limit=20,
        offset=0,
        only_ready=True,
        report_type="zongbao",
    )
    after_search = duty_filter_adapter.manual_reviews.search_candidates(
        owner_user_id=SECOND_OWNER_USER_ID,
        query="Needle",
        limit=20,
        offset=0,
    )

    assert after_pending == before_pending
    assert after_selected == before_selected
    assert after_search == before_search


def test_cancelled_and_active_shift_rows_on_one_article_count_as_processed(
    duty_filter_adapter: PostgresAdapter,
) -> None:
    user_id = "00000000-0000-0000-0000-000000000001"
    active_shift_id = "00000000-0000-0000-0000-000000000011"
    cancelled_shift_id = "00000000-0000-0000-0000-000000000013"

    with duty_filter_adapter._cursor() as cur:
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
                (cancelled_shift_id, UNPROCESSED, "discarded", user_id, user_id),
                (active_shift_id, UNPROCESSED, "selected", user_id, user_id),
            ],
        )

    rows, total = duty_filter_adapter.manual_reviews.fetch(
        owner_user_id=OWNER_USER_ID,
        status="pending",
        limit=20,
        offset=0,
        only_ready=True,
        duty_unprocessed_only=True,
    )

    # 在岗班次那条记录使它算作已处理；两条记录不得导致重复计数
    assert _ids(rows) == {CANCELLED}
    assert total == 1
