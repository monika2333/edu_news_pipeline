from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Optional

import pytest

from src.adapters import db_postgres_manual_reviews, db_postgres_news_summaries


class FakeCursor:
    def __init__(self) -> None:
        self.rowcount = 1
        self.query: Optional[str] = None
        self.params: Optional[tuple[Any, ...]] = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params


class FakeFetchCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> dict[str, int]:
        return {"total": 0}

    def fetchall(self) -> list[dict[str, Any]]:
        return []


class FakeStatusCountsCursor:
    def __init__(self) -> None:
        self.query: Optional[str] = None
        self.params: Optional[tuple[Any, ...]] = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.query = query
        self.params = params

    def fetchone(self) -> dict[str, int]:
        return {
            "pending": 7,
            "selected": 3,
            "backup": 2,
            "discarded": 5,
            "exported": 1,
        }


class FakeVersionedReviewCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.update_params: list[tuple[Any, ...]] = []
        self._current_article_id: Optional[str] = None

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        if "FROM manual_reviews" in query and "FOR UPDATE" in query:
            return
        self.update_params.append(params)
        self._current_article_id = str(params[-2])

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.rows]

    def fetchone(self) -> Optional[dict[str, Any]]:
        if self._current_article_id is None:
            return None
        current = next(
            row
            for row in self.rows
            if row["article_id"] == self._current_article_id
        )
        params = self.update_params[-1]
        return {
            **current,
            "status": params[0],
            "rank": params[1],
            "report_type": params[5] or current["report_type"],
            "version": current["version"] + 1,
        }


class FakeUpdateCursor:
    def __init__(self) -> None:
        self.rowcount = 0
        self.query: Optional[str] = None
        self.payload: list[tuple[Any, ...]] = []

    def executemany(self, query: str, payload: list[tuple[Any, ...]]) -> None:
        self.query = query
        self.payload = payload
        self.rowcount = len(payload)


class FakeEnqueueCursor:
    def __init__(self, article: Optional[dict[str, Any]]) -> None:
        self.article = article
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> Optional[dict[str, Any]]:
        return self.article


class FakeDutyImportCursor:
    def __init__(self, shift_rows: list[dict[str, Any]]) -> None:
        self.shift_rows = shift_rows
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchall(self) -> list[dict[str, Any]]:
        return self.shift_rows

    def fetchone(self) -> dict[str, Any]:
        if "MAX(rank)" in self.queries[-1]:
            return {"max_rank": 3}
        return {
            "article_id": "article-1",
            "status": "selected",
            "summary": "已处理摘要",
            "manual_llm_source": "已处理来源",
            "version": 5,
        }


def test_enqueue_manual_review_requires_completed_external_score() -> None:
    cur = FakeEnqueueCursor(
        {
            "external_importance_score": 0,
            "external_importance_checked_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
        }
    )

    db_postgres_manual_reviews.enqueue_manual_review(cur, "article-1")

    assert len(cur.queries) == 2
    assert "SELECT external_importance_score" in cur.queries[0]
    assert "INSERT INTO manual_reviews" in cur.queries[1]
    assert cur.params[0] == ("article-1",)


@pytest.mark.parametrize(
    "article",
    [
        None,
        {
            "external_importance_score": None,
            "external_importance_checked_at": datetime(2026, 7, 22, tzinfo=timezone.utc),
        },
        {
            "external_importance_score": 80,
            "external_importance_checked_at": None,
        },
    ],
)
def test_enqueue_manual_review_rejects_missing_or_unscored_article(
    article: Optional[dict[str, Any]],
) -> None:
    cur = FakeEnqueueCursor(article)

    with pytest.raises(ValueError):
        db_postgres_manual_reviews.enqueue_manual_review(cur, "article-1")

    assert len(cur.queries) == 1


def test_fetch_review_buckets_for_update_locks_all_review_rows() -> None:
    cur = FakeFetchCursor()

    rows = db_postgres_manual_reviews.fetch_review_buckets_for_update(
        cur,
        owner_user_id="admin-1",
    )

    assert rows == []
    assert len(cur.queries) == 1
    query = cur.queries[0]
    assert "mr.status IN (%s, %s)" in query
    assert "mr.status AS previous_status" in query
    assert "COALESCE(mr.report_type, 'zongbao') AS report_type" in query
    assert "FOR UPDATE OF mr" in query
    assert "ready_for_export" not in query
    assert cur.params[0] == ("admin-1", "selected", "backup")


def test_fetch_manual_reviews_orders_selected_items_by_manual_rank_first() -> None:
    cur = FakeFetchCursor()

    rows, total = db_postgres_manual_reviews.fetch_manual_reviews(
        cur,
        owner_user_id="admin-1",
        status="selected",
        limit=20,
        offset=0,
        report_type="zongbao",
    )

    assert rows == []
    assert total == 0
    assert len(cur.queries) == 2
    list_query = cur.queries[1]
    assert "LEFT JOIN score_feedbacks sf" in list_query
    assert "sf.prompt_key = ns.external_importance_raw ->> 'prompt_key'" in list_query
    assert "sf.prompt_version = ns.external_importance_raw ->> 'prompt_version'" in list_query
    rank_index = list_query.index("mr.rank ASC NULLS LAST")
    score_index = list_query.index("ns.external_importance_score DESC NULLS LAST")
    assert rank_index < score_index


def test_fetch_manual_reviews_applies_search_to_count_and_page_queries() -> None:
    cur = FakeFetchCursor()

    db_postgres_manual_reviews.fetch_manual_reviews(
        cur,
        owner_user_id="admin-1",
        status="discarded",
        limit=10,
        offset=5,
        query="  教育政策  ",
        order_by_decided_at=True,
    )

    assert len(cur.queries) == 2
    assert all("coalesce(ns.title, '')" in query for query in cur.queries)
    assert all("coalesce(ns.llm_summary, '')" in query for query in cur.queries)
    assert all("coalesce(ns.content_markdown, '')" in query for query in cur.queries)
    assert all("ILIKE %s" in query for query in cur.queries)
    assert cur.params[0] == ("admin-1", "discarded", "%教育政策%")
    assert cur.params[1] == ("admin-1", "discarded", "%教育政策%", 10, 5)


def test_fetch_manual_cluster_sources_reads_latest_ready_news_without_manual_reviews() -> None:
    cur = FakeFetchCursor()

    db_postgres_manual_reviews.fetch_manual_cluster_sources(cur)

    assert cur.params[-1] == (5000,)
    assert "FROM news_summaries" in cur.queries[-1]
    assert "manual_reviews" not in cur.queries[-1]
    assert "status = 'ready_for_export'" in cur.queries[-1]
    assert "ORDER BY created_at DESC" in cur.queries[-1]


def test_manual_review_status_counts_only_scopes_report_states() -> None:
    cur = FakeStatusCountsCursor()

    counts = db_postgres_manual_reviews.manual_review_status_counts(
        cur,
        owner_user_id="admin-1",
        report_type="wanbao",
    )

    assert counts == {
        "pending": 7,
        "selected": 3,
        "backup": 2,
        "discarded": 5,
        "exported": 1,
    }
    assert cur.query is not None
    assert "COUNT(*) FILTER (WHERE status = 'pending')" in cur.query
    assert "COUNT(*) FILTER (WHERE status = 'discarded')" in cur.query
    assert "WHERE COALESCE(report_type, 'zongbao') = %s" not in cur.query
    assert cur.params == ("wanbao", "wanbao", "wanbao", "admin-1")


def test_versioned_decide_preserves_report_type_for_shared_states() -> None:
    cur = FakeVersionedReviewCursor(
        [
            {
                "article_id": "selected-1",
                "status": "pending",
                "report_type": "zongbao",
                "version": 2,
            },
            {
                "article_id": "pending-1",
                "status": "selected",
                "report_type": "zongbao",
                "version": 4,
            },
        ]
    )

    _, after = db_postgres_manual_reviews.update_manual_review_statuses_with_versions(
        cur,
        [
            {
                "article_id": "selected-1",
                "status": "selected",
                "rank": 1.0,
                "report_type": "wanbao",
            },
            {
                "article_id": "pending-1",
                "status": "pending",
                "rank": None,
                "report_type": None,
            },
        ],
        actor_username="admin",
        actor_user_id="admin-id",
        owner_user_id="admin-id",
        expected_versions={"selected-1": 2, "pending-1": 4},
        require_versions=True,
        report_type=None,
    )

    assert cur.update_params[0][5] == "wanbao"
    assert cur.update_params[1][5] is None
    assert after[0]["report_type"] == "wanbao"
    assert after[1]["report_type"] == "zongbao"


def test_duty_import_can_keep_edited_existing_version_without_moving_it() -> None:
    cur = FakeDutyImportCursor(
        [
            {
                "article_id": "article-1",
                "edited_summary": "值班摘要",
                "manual_llm_source": "值班来源",
                "notes": None,
                "llm_summary": "机器摘要",
                "score": 80,
            }
        ]
    )

    result = db_postgres_manual_reviews.import_shift_reviews_into_manual(
        cur,
        shift_id="shift-1",
        article_ids=["article-1"],
        target_status="backup",
        report_type="wanbao",
        actor_username="admin",
        actor_user_id="admin-id",
        existing_reviews=[
            {
                "article_id": "article-1",
                "status": "selected",
                "report_type": "zongbao",
                "summary": "管理员摘要",
                "manual_llm_source": "管理员来源",
                "version": 4,
            }
        ],
        conflict_resolutions={
            "article-1": {
                "choice": "existing",
                "summary": "编辑后的管理员摘要",
                "manual_llm_source": "编辑后的管理员来源",
                "existing_version": 4,
            }
        },
    )

    update_query = cur.queries[-1]
    assert result[0]["article_id"] == "article-1"
    assert "UPDATE manual_reviews" in update_query
    assert "status = %s" not in update_query
    assert cur.params[-1][:2] == (
        "编辑后的管理员摘要",
        "编辑后的管理员来源",
    )


def test_duty_import_requires_resolution_for_existing_manual_review() -> None:
    cur = FakeDutyImportCursor(
        [
            {
                "article_id": "article-1",
                "edited_summary": "值班摘要",
                "manual_llm_source": "值班来源",
                "notes": None,
                "llm_summary": "机器摘要",
                "score": 80,
            }
        ]
    )

    with pytest.raises(
        db_postgres_manual_reviews.ManualReviewConflictError,
        match="请先选择保留版本",
    ):
        db_postgres_manual_reviews.import_shift_reviews_into_manual(
            cur,
            shift_id="shift-1",
            article_ids=["article-1"],
            target_status="selected",
            report_type="zongbao",
            actor_username="admin",
            actor_user_id="admin-id",
            existing_reviews=[
                {
                    "article_id": "article-1",
                    "version": 4,
                }
            ],
            conflict_resolutions={},
        )


def test_duty_import_moves_pending_candidate_without_conflict_prompt() -> None:
    cur = FakeDutyImportCursor(
        [
            {
                "article_id": "article-1",
                "edited_summary": "值班摘要",
                "manual_llm_source": "值班来源",
                "notes": None,
                "llm_summary": "机器摘要",
                "score": 80,
            }
        ]
    )

    db_postgres_manual_reviews.import_shift_reviews_into_manual(
        cur,
        shift_id="shift-1",
        article_ids=["article-1"],
        target_status="selected",
        report_type="zongbao",
        actor_username="admin",
        actor_user_id="admin-id",
        existing_reviews=[
            {
                "article_id": "article-1",
                "status": "pending",
                "version": 1,
            }
        ],
        conflict_resolutions={},
    )

    assert "status = %s" in cur.queries[-1]
    assert cur.params[-1][0] == "selected"


def test_duty_import_reopens_discarded_candidate_without_conflict_prompt() -> None:
    cur = FakeDutyImportCursor(
        [
            {
                "article_id": "article-1",
                "edited_summary": "值班摘要",
                "manual_llm_source": "值班来源",
                "notes": None,
                "llm_summary": "机器摘要",
                "score": 80,
            }
        ]
    )

    db_postgres_manual_reviews.import_shift_reviews_into_manual(
        cur,
        shift_id="shift-1",
        article_ids=["article-1"],
        target_status="selected",
        report_type="zongbao",
        actor_username="admin",
        actor_user_id="admin-id",
        existing_reviews=[
            {
                "article_id": "article-1",
                "status": "discarded",
                "version": 2,
            }
        ],
        conflict_resolutions={},
    )

    assert "status = %s" in cur.queries[-1]
    assert cur.params[-1][0] == "selected"
    assert cur.params[-1][1] == "值班摘要"


def test_duty_import_rejects_discarded_target() -> None:
    cur = FakeDutyImportCursor(
        [
            {
                "article_id": "article-1",
                "edited_summary": "值班摘要",
                "manual_llm_source": "值班来源",
                "notes": None,
                "llm_summary": "机器摘要",
                "score": 80,
            }
        ]
    )

    with pytest.raises(ValueError, match="selected or backup"):
        db_postgres_manual_reviews.import_shift_reviews_into_manual(
            cur,
            shift_id="shift-1",
            article_ids=["article-1"],
            target_status="discarded",
            report_type="zongbao",
            actor_username="admin",
            actor_user_id="admin-id",
            existing_reviews=[],
            conflict_resolutions={},
        )

    assert cur.queries == []


def test_duty_import_can_replace_existing_version_after_explicit_choice() -> None:
    cur = FakeDutyImportCursor(
        [
            {
                "article_id": "article-1",
                "edited_summary": "值班摘要",
                "manual_llm_source": "值班来源",
                "notes": "值班备注",
                "llm_summary": "机器摘要",
                "score": 80,
            }
        ]
    )

    db_postgres_manual_reviews.import_shift_reviews_into_manual(
        cur,
        shift_id="shift-1",
        article_ids=["article-1"],
        target_status="backup",
        report_type="wanbao",
        actor_username="admin",
        actor_user_id="admin-id",
        existing_reviews=[
            {
                "article_id": "article-1",
                "status": "selected",
                "report_type": "zongbao",
                "summary": "管理员摘要",
                "manual_llm_source": "管理员来源",
                "version": 4,
            }
        ],
        conflict_resolutions={
            "article-1": {
                "choice": "duty",
                "summary": "编辑后的值班摘要",
                "manual_llm_source": "编辑后的值班来源",
                "existing_version": 4,
            }
        },
    )

    update_query = cur.queries[-1]
    assert "status = %s" in update_query
    assert cur.params[-1][0] == "backup"
    assert cur.params[-1][1] == "编辑后的值班摘要"
    assert cur.params[-1][7] == "编辑后的值班来源"
    assert cur.params[-1][8] == "wanbao"


def test_update_summary_categories_updates_canonical_group_fields() -> None:
    cur = FakeUpdateCursor()

    updated = db_postgres_news_summaries.update_summary_categories(
        cur,
        [
            {
                "article_id": "a1",
                "is_beijing_related": True,
                "sentiment_label": "positive",
            },
            {
                "article_id": "a2",
                "is_beijing_related": False,
                "sentiment_label": "negative",
            },
        ],
    )

    assert updated == 2
    assert cur.query is not None
    assert "UPDATE news_summaries" in cur.query
    assert cur.payload == [(True, "positive", "a1"), (False, "negative", "a2")]


class _ScriptedVersionedCursor:
    """Cursor that answers the real version-check queries with scripted rows.

    Unlike FakeVersionedReviewCursor, the UPDATE result is supplied by the test
    instead of being derived from the row, so a stale version is expressible and
    the conflict raise sites are actually reached.
    """

    def __init__(
        self,
        stored_version: int,
        *,
        update_row: Optional[dict[str, Any]],
    ) -> None:
        self.stored_version = stored_version
        self.update_row = update_row
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.executed.append((query, params))

    def fetchall(self) -> list[dict[str, Any]]:
        # fetch_manual_review_rows(cur, ..., for_update=True)
        return [
            {
                "id": 1,
                "article_id": "a1",
                "status": "selected",
                "summary": "摘要",
                "rank": 1.0,
                "notes": None,
                "score": None,
                "decided_by": "editor",
                "decided_by_user_id": None,
                "decided_at": None,
                "manual_llm_source": None,
                "report_type": "zongbao",
                "version": self.stored_version,
                "created_at": None,
                "updated_at": None,
            }
        ]

    def fetchone(self) -> Optional[dict[str, Any]]:
        # Result of the version-guarded UPDATE.
        return self.update_row


def _stale_update() -> tuple[_ScriptedVersionedCursor, list[dict[str, Any]]]:
    updates = [{"article_id": "a1", "status": "discarded", "rank": None}]
    return _ScriptedVersionedCursor(5, update_row=None), updates


def test_version_check_rejects_stale_expected_version() -> None:
    """A client holding an outdated version must get ManualReviewConflictError.

    Guards the import of ManualReviewConflictError into _versions.py: without it
    the raise becomes a NameError, and the isinstance() handlers in
    manual_filter_routes.py / admin_summary_routes.py stop matching, so two
    administrators editing the same article concurrently get a 500 instead of a
    409.
    """
    cur, updates = _stale_update()

    with pytest.raises(db_postgres_manual_reviews.ManualReviewConflictError) as excinfo:
        db_postgres_manual_reviews.update_manual_review_statuses_with_versions(
            cur,
            updates,
            actor_username="admin",
            actor_user_id="admin-id",
            owner_user_id="admin-id",
            expected_versions={"a1": 3},
            require_versions=True,
        )

    assert not isinstance(excinfo.value, NameError)
    assert "a1" in str(excinfo.value)
    # The pre-check must fire before any write is attempted.
    assert not any("UPDATE manual_reviews" in query for query, _ in cur.executed)


def test_version_check_rejects_row_changed_by_concurrent_writer() -> None:
    """The version matched at read time but the guarded UPDATE matched nothing."""
    cur, updates = _stale_update()
    cur.update_row = None

    with pytest.raises(db_postgres_manual_reviews.ManualReviewConflictError) as excinfo:
        db_postgres_manual_reviews.update_manual_review_statuses_with_versions(
            cur,
            updates,
            actor_username="admin",
            actor_user_id="admin-id",
            owner_user_id="admin-id",
            expected_versions={"a1": 5},
            require_versions=True,
        )

    assert not isinstance(excinfo.value, NameError)
    assert "a1" in str(excinfo.value)
    # Reaching the raise here proves the read/validate path ran and the guarded
    # UPDATE was actually issued with the version read from the row.
    update_params = [
        params for query, params in cur.executed if "UPDATE manual_reviews" in query
    ]
    assert len(update_params) == 1
    assert update_params[0][-1] == 5



def test_fetch_manual_reviews_applies_refine_filters_with_param_order() -> None:
    cur = FakeFetchCursor()

    db_postgres_manual_reviews.fetch_manual_reviews(
        cur,
        owner_user_id="admin-1",
        status="pending",
        limit=10,
        offset=0,
        hour_from=8,
        hour_to=12,
        duplicate_state="untagged",
        min_score=60,
        max_score=95,
    )

    hour_expr = "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai')"
    assert len(cur.queries) == 2
    assert all(f"{hour_expr} >= %s" in query for query in cur.queries)
    assert all(f"{hour_expr} <= %s" in query for query in cur.queries)
    assert all("NOT EXISTS (" in query for query in cur.queries)
    assert all("sdm.state <> 'dismissed'" in query for query in cur.queries)
    assert all("ns.external_importance_score >= %s" in query for query in cur.queries)
    assert all("ns.external_importance_score <= %s" in query for query in cur.queries)
    # 子句参数在 owner/status 之后；limit/offset 只追加在列表查询末尾
    assert cur.params[0] == ("admin-1", "pending", 8, 12, 60, 95)
    assert cur.params[1] == ("admin-1", "pending", 8, 12, 60, 95, 10, 0)


def test_fetch_manual_clusters_applies_refine_filters_inside_pending_cte() -> None:
    cur = FakeFetchCursor()

    rows = db_postgres_manual_reviews.fetch_manual_clusters(
        cur,
        owner_user_id="admin-1",
        bucket_key="internal_positive",
        hour_from=22,
        hour_to=6,
        duplicate_state="untagged",
        min_score=60,
    )

    assert rows == []
    query = cur.queries[0]
    assert "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') >= %s" in query
    assert "EXTRACT(HOUR FROM ns.created_at AT TIME ZONE 'Asia/Shanghai') <= %s" in query
    assert "NOT EXISTS (" in query
    assert "ns.external_importance_score >= %s" in query
    # 子句参数插在 owner 与 singleton bucket 参数之间：
    # (bucket, bucket, owner, *细化参数, bucket, bucket)
    assert cur.params[0] == (
        "internal_positive", "internal_positive", "admin-1", 22, 6, 60,
        "internal_positive", "internal_positive",
    )


def test_restore_discarded_by_filter_scopes_owner_status_and_time() -> None:
    """真实 SQL 语义：owner/status 收口、上海日期边界、微秒批次相等。

    夹具含一条其他管理员的已放弃行与一条同一管理员的 pending 行，
    两者在按条件恢复中都必须原样保留。
    """
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    from src.config import get_settings

    settings = get_settings()
    owner_a = "11111111-1111-1111-1111-111111111111"
    owner_b = "22222222-2222-2222-2222-222222222222"
    a2_batch_ts = "2026-10-06T23:59:00.123456+08:00"

    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=False,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            for table in ("manual_reviews", "news_summaries"):
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"(LIKE public.{table} INCLUDING DEFAULTS) ON COMMIT DROP"
                )
            cur.execute(
                "ALTER TABLE manual_reviews "
                "ADD COLUMN IF NOT EXISTS owner_user_id uuid NOT NULL"
            )
            for article_id, score in (("a1", 80), ("a2", 90), ("a3", 70), ("b1", 95)):
                cur.execute(
                    """
                    INSERT INTO news_summaries
                        (article_id, title, status, external_importance_score)
                    VALUES (%s, %s, 'ready_for_export', %s)
                    """,
                    (article_id, f"标题{article_id}", score),
                )

            cur.execute(
                """
                INSERT INTO manual_reviews
                    (owner_user_id, article_id, status, version, rank,
                     decided_at, report_type)
                VALUES
                    (%s, 'a1', 'discarded', 3, 2.0,
                     '2026-10-07T00:15:00+08:00', 'zongbao'),
                    (%s, 'a2', 'discarded', 2, NULL,
                     %s, 'zongbao'),
                    (%s, 'a3', 'pending', 1, NULL, NULL, NULL),
                    (%s, 'b1', 'discarded', 5, NULL,
                     '2026-10-07T08:00:00+08:00', 'zongbao')
                """,
                (owner_a, owner_a, a2_batch_ts, owner_a, owner_b),
            )

            def fetch_row(article_id: str) -> dict[str, Any]:
                cur.execute(
                    "SELECT status, version, rank, decided_at, report_type "
                    "FROM manual_reviews WHERE article_id = %s",
                    (article_id,),
                )
                return dict(cur.fetchone())

            # dry_run：只计数（owner_a 有 a1/a2 两条已放弃），不写入
            result = db_postgres_manual_reviews.restore_discarded_manual_reviews_by_filter(
                cur,
                owner_user_id=owner_a,
                actor_username="admin-a",
                actor_user_id=owner_a,
                dry_run=True,
            )
            assert result["matched"] == 2
            assert result["updated"] == 0
            assert fetch_row("a1")["status"] == "discarded"

            # 上海本地日期边界：a1 本地日期 10-07 命中，a2 本地日期 10-06 不命中
            result = db_postgres_manual_reviews.restore_discarded_manual_reviews_by_filter(
                cur,
                owner_user_id=owner_a,
                actor_username="admin-a",
                actor_user_id=owner_a,
                decided_since=date(2026, 10, 7),
                dry_run=False,
            )
            assert result["matched"] == 1
            assert result["updated"] == 1

            a1 = fetch_row("a1")
            assert a1["status"] == "pending"
            assert a1["version"] == 4
            assert a1["rank"] is None
            assert a1["decided_at"] is not None
            # report_type 与 /decide 恢复到待处理一致：COALESCE 保留原值
            assert a1["report_type"] == "zongbao"

            a2 = fetch_row("a2")
            assert a2["status"] == "discarded"
            assert a2["version"] == 2
            assert str(a2["decided_at"]) .startswith("2026-10-06")

            # 其他管理员的行与自己的 pending 行都不受影响
            assert fetch_row("b1")["status"] == "discarded"
            assert fetch_row("b1")["version"] == 5
            assert fetch_row("a3")["status"] == "pending"

            # 微秒批次：精确相等才命中，差一微秒不命中
            batch = datetime.fromisoformat(a2_batch_ts)
            result = db_postgres_manual_reviews.restore_discarded_manual_reviews_by_filter(
                cur,
                owner_user_id=owner_a,
                actor_username="admin-a",
                actor_user_id=owner_a,
                batch_decided_at=batch,
                dry_run=True,
            )
            assert result["matched"] == 1
            result = db_postgres_manual_reviews.restore_discarded_manual_reviews_by_filter(
                cur,
                owner_user_id=owner_a,
                actor_username="admin-a",
                actor_user_id=owner_a,
                batch_decided_at=batch,
                dry_run=False,
            )
            assert result["updated"] == 1
            assert fetch_row("a2")["status"] == "pending"


class _CaptureCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.params: list[tuple[Any, ...]] = []
        self.rows: list[dict[str, Any]] = []

    def execute(self, query: str, params: tuple[Any, ...]) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> dict[str, Any]:
        return {"total": 0}

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


def test_restore_filter_matches_discarded_list_where_construction() -> None:
    """T6（管理员）：同一组条件下，放弃列表与按条件恢复的子句与参数一致。"""
    filters = {
        "region": "internal",
        "sentiment": "negative",
        "query": "教育政策",
        "min_score": 5.5,
        "max_score": 30,
        "decided_since": date(2026, 10, 1),
        "batch_decided_at": datetime(
            2026, 10, 7, 6, 32, 5, 123456, tzinfo=timezone.utc
        ),
    }

    list_cursor = _CaptureCursor()
    db_postgres_manual_reviews.fetch_manual_reviews(
        list_cursor,
        owner_user_id="owner-1",
        status="discarded",
        limit=30,
        offset=0,
        order_by_decided_at=True,
        **filters,
    )

    restore_cursor = _CaptureCursor()
    db_postgres_manual_reviews.restore_discarded_manual_reviews_by_filter(
        restore_cursor,
        owner_user_id="owner-1",
        actor_username="admin-a",
        actor_user_id="owner-1",
        dry_run=True,
        **filters,
    )

    list_sql = list_cursor.queries[0]
    restore_sql = restore_cursor.queries[0]
    expected_clauses = [
        "ns.is_beijing_related = %s",
        "ns.sentiment_label = %s",
        "ILIKE %s",
        "ns.external_importance_score >= %s",
        "ns.external_importance_score <= %s",
        "(mr.decided_at AT TIME ZONE 'Asia/Shanghai')::date >= %s",
        "mr.decided_at = %s",
        "mr.owner_user_id = %s",
        "mr.status = %s",
    ]
    for clause in expected_clauses:
        assert clause in list_sql, f"列表 SQL 缺少子句：{clause}"
        assert clause in restore_sql, f"恢复 SQL 缺少子句：{clause}"
    # 恢复的锁定查询与列表 count 查询的参数完全一致（owner + status + 过滤参数）
    assert restore_cursor.params[0] == list_cursor.params[0]


def test_fetch_discarded_batches_scopes_owner_and_roundtrips() -> None:
    """T8：只返回 >=2 的组、倒序取 10、owner/status 收口；
    T9：批次 decided_at 原样作为 batch_decided_at 查列表，total == count。"""
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    from src.config import get_settings

    settings = get_settings()
    owner_a = "44444444-4444-4444-4444-444444444444"
    owner_b = "55555555-5555-5555-5555-555555555555"
    batch_x = "2026-10-07T09:30:00.123456+08:00"
    batch_y = "2026-10-06T08:00:00+08:00"
    batch_z = "2026-10-05T08:00:00+08:00"

    with psycopg.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        dbname=settings.db_name,
        autocommit=False,
        row_factory=dict_row,
    ) as conn:
        with conn.cursor() as cur:
            for table in ("manual_reviews", "news_summaries"):
                cur.execute(
                    f"CREATE TEMP TABLE {table} "
                    f"(LIKE public.{table} INCLUDING DEFAULTS) ON COMMIT DROP"
                )
            cur.execute(
                """
                INSERT INTO manual_reviews
                    (owner_user_id, article_id, status, version, decided_at)
                VALUES
                    -- 批次 X：3 条，带非零微秒
                    (%s, 'x1', 'discarded', 1, %s),
                    (%s, 'x2', 'discarded', 1, %s),
                    (%s, 'x3', 'discarded', 1, %s),
                    -- 批次 Y / Z：各 2 条
                    (%s, 'y1', 'discarded', 1, %s),
                    (%s, 'y2', 'discarded', 1, %s),
                    (%s, 'z1', 'discarded', 1, %s),
                    (%s, 'z2', 'discarded', 1, %s),
                    -- 单条批次：不满足 >= 2
                    (%s, 's1', 'discarded', 1, '2026-10-07T10:00:00+08:00'),
                    -- decided_at 为空：不参与分组
                    (%s, 'n1', 'discarded', 1, NULL),
                    (%s, 'n2', 'discarded', 1, NULL),
                    -- 非 discarded 状态：即使同 decided_at 也不计
                    (%s, 'p1', 'pending', 1, %s),
                    (%s, 'p2', 'pending', 1, %s),
                    -- 其他管理员：不属于当前工作区
                    (%s, 'b1', 'discarded', 1, '2026-10-07T11:00:00+08:00'),
                    (%s, 'b2', 'discarded', 1, '2026-10-07T11:00:00+08:00'),
                    (%s, 'b3', 'discarded', 1, '2026-10-07T11:00:00+08:00')
                """,
                (
                    owner_a, batch_x, owner_a, batch_x, owner_a, batch_x,
                    owner_a, batch_y, owner_a, batch_y,
                    owner_a, batch_z, owner_a, batch_z,
                    owner_a,
                    owner_a, owner_a,
                    owner_a, batch_x, owner_a, batch_x,
                    owner_b, owner_b, owner_b,
                ),
            )

            # 放弃列表 INNER JOIN news_summaries，为每条 manual_review 补齐新闻行
            cur.execute(
                """
                INSERT INTO news_summaries (article_id, title, status)
                SELECT mr.article_id, '标题' || mr.article_id, 'ready_for_export'
                FROM manual_reviews mr
                """
            )

            batches = db_postgres_manual_reviews.fetch_discarded_batches(
                cur,
                owner_user_id=owner_a,
            )

            assert [batch["count"] for batch in batches] == [3, 2, 2]
            assert batches[0]["decided_at"] == datetime.fromisoformat(batch_x)
            # 微秒精度必须原样保留（往返一致的前提）
            assert batches[0]["decided_at"].microsecond == 123456

            # T9：把接口返回的 decided_at 原样作为 batch_decided_at 查放弃列表
            _, total = db_postgres_manual_reviews.fetch_manual_reviews(
                cur,
                owner_user_id=owner_a,
                status="discarded",
                limit=200,
                offset=0,
                batch_decided_at=batches[0]["decided_at"],
            )
            assert total == 3
