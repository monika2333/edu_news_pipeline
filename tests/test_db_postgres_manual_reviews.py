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


def test_discard_manual_candidates_before_date_places_filter_params_first() -> None:
    cur = FakeCursor()
    decided_at = datetime(2025, 1, 3, 8, 0, tzinfo=timezone.utc)

    updated = db_postgres_manual_reviews.discard_manual_candidates_before_date(
        cur,
        region="internal",
        sentiment="positive",
        query="keyword",
        published_before=date(2025, 1, 2),
        actor="tester",
        decided_at=decided_at,
        report_type="zongbao",
    )

    assert updated == 1
    assert cur.query is not None
    assert "decided_by = %s" in cur.query
    assert "WHERE mr.status = %s" in cur.query
    assert cur.params is not None
    assert cur.params[:6] == ("pending", "zongbao", True, "positive", "%keyword%", date(2025, 1, 2))
    assert cur.params[6] == "tester"
    assert cur.params[7] == decided_at


def test_fetch_manual_reviews_orders_selected_items_by_manual_rank_first() -> None:
    cur = FakeFetchCursor()

    rows, total = db_postgres_manual_reviews.fetch_manual_reviews(
        cur,
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


def test_fetch_manual_clusters_can_hide_submitted_members() -> None:
    cur = FakeFetchCursor()

    db_postgres_manual_reviews.fetch_manual_clusters(
        cur,
        bucket_key="internal_positive",
        hide_submitted=True,
    )

    query = cur.queries[-1]
    assert "FROM submission_duplicate_matches sdm" in query
    assert "sdm.state IN ('confirmed', 'suspected')" in query
    assert cur.params[-1] == (
        "internal_positive",
        "internal_positive",
        True,
    )


def test_fetch_manual_pending_for_cluster_ignores_report_type() -> None:
    cur = FakeFetchCursor()

    db_postgres_manual_reviews.fetch_manual_pending_for_cluster(
        cur,
        region="internal",
        sentiment="positive",
        report_type="wanbao",
    )

    assert cur.params[-1] == ("pending", True, "positive", 5000)
    assert "COALESCE(mr.report_type, 'zongbao') = %s" not in cur.queries[-1]


def test_manual_review_status_counts_only_scopes_report_states() -> None:
    cur = FakeStatusCountsCursor()

    counts = db_postgres_manual_reviews.manual_review_status_counts(
        cur,
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
    assert cur.params == ("wanbao", "wanbao", "wanbao")


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
