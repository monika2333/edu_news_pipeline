from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.adapters import db_postgres_shift_reviews
from src.console import admin_summary_service
from src.console.auth_service import ConsoleUser


class FakeAdminSummaryAdapter:
    def __init__(self) -> None:
        self.review_query: dict[str, Any] = {}
        self.import_query: dict[str, Any] = {}
        self.bulk_discard_query: dict[str, Any] = {}
        self.preview_rows: list[dict[str, Any]] = []

    def fetch_duty_shift(self, shift_id: str) -> dict[str, str]:
        return {"id": shift_id}

    def fetch_shift_review_items(
        self,
        *,
        shift_id: str,
        decision: Optional[str],
        report_type: Optional[str],
        limit: int,
        offset: int,
        mismatch_only: bool,
        include_admin_state: bool,
        admin_discarded_only: bool,
        exclude_admin_discarded: bool,
    ) -> tuple[list[dict[str, Any]], int]:
        self.review_query = {
            "shift_id": shift_id,
            "decision": decision,
            "report_type": report_type,
            "limit": limit,
            "offset": offset,
            "mismatch_only": mismatch_only,
            "include_admin_state": include_admin_state,
            "admin_discarded_only": admin_discarded_only,
            "exclude_admin_discarded": exclude_admin_discarded,
        }
        return [
            {
                "article_id": "article-1",
                "decision": "backup",
                "report_type": "wanbao",
                "version": 2,
                "title": "测试新闻",
                "llm_summary": "机器摘要",
                "score_details": {},
                "admin_status": "selected",
                "admin_report_type": "zongbao",
                "admin_version": 4,
                "admin_discarded_at": (
                    "2026-07-26T05:00:00+00:00"
                    if admin_discarded_only
                    else None
                ),
                "admin_discarded_by_user_id": (
                    "admin-id"
                    if admin_discarded_only
                    else None
                ),
                "admin_discarded_by_display_name": (
                    "管理员"
                    if admin_discarded_only
                    else None
                ),
            }
        ], 1

    def preview_shift_reviews_for_manual(
        self,
        *,
        shift_id: str,
        article_ids: list[str],
    ) -> list[dict[str, Any]]:
        self.import_query = {
            "shift_id": shift_id,
            "article_ids": article_ids,
        }
        return self.preview_rows

    def import_shift_reviews_into_manual(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.import_query = kwargs
        return [{"article_id": "article-1"}]

    def set_shift_review_admin_discarded(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.import_query = kwargs
        return {
            "article_id": kwargs["article_id"],
            "decision": "selected",
            "report_type": "zongbao",
            "admin_discarded_at": "2026-07-26T05:00:00+00:00",
            "admin_discarded_by_user_id": kwargs["actor_user_id"],
            "version": 1,
            "score_details": {},
        }

    def set_shift_reviews_admin_discarded(
        self,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.bulk_discard_query = kwargs
        return [
            {
                "article_id": article_id,
                "decision": "selected",
                "report_type": "zongbao",
                "admin_discarded_at": "2026-07-26T05:00:00+00:00",
                "admin_discarded_by_user_id": kwargs["actor_user_id"],
                "version": 1,
                "score_details": {},
            }
            for article_id in kwargs["article_ids"]
        ]


def test_shift_results_requests_and_returns_admin_mismatch_state(
    monkeypatch,
) -> None:
    adapter = FakeAdminSummaryAdapter()
    monkeypatch.setattr(admin_summary_service, "get_adapter", lambda: adapter)

    result = admin_summary_service.list_shift_results(
        shift_id="shift-1",
        decision=None,
        report_type=None,
        mismatch_only=True,
        limit=200,
        offset=0,
    )

    assert adapter.review_query["mismatch_only"] is True
    assert adapter.review_query["include_admin_state"] is True
    assert adapter.review_query["exclude_admin_discarded"] is True
    assert result["items"][0]["admin_status"] == "selected"
    assert result["items"][0]["admin_report_type"] == "zongbao"


def test_shift_summaries_exclude_future_and_sort_latest_first(monkeypatch) -> None:
    now = datetime(2026, 7, 25, 8, tzinfo=timezone.utc)

    class SummaryAdapter:
        def fetch_admin_shift_summaries(self, *, limit: int) -> list[dict[str, Any]]:
            del limit
            return [
                {
                    "shift_id": "previous",
                    "starts_at": now - timedelta(days=2),
                    "ends_at": now - timedelta(days=1),
                },
                {
                    "shift_id": "future",
                    "starts_at": now + timedelta(hours=1),
                    "ends_at": now + timedelta(days=1, hours=1),
                },
                {
                    "shift_id": "today",
                    "starts_at": now - timedelta(hours=10),
                    "ends_at": now + timedelta(hours=10),
                    "zongbao_selected": "3",
                    "zongbao_backup": 2,
                    "wanbao_selected": 7,
                    "wanbao_backup": 4,
                },
            ]

    monkeypatch.setattr(
        admin_summary_service,
        "get_adapter",
        lambda: SummaryAdapter(),
    )

    result = admin_summary_service.list_shift_summaries(limit=60, now=now)

    assert [item["shift_id"] for item in result] == ["today", "previous"]
    assert result[0]["zongbao_selected"] == 3
    assert result[0]["zongbao_backup"] == 2
    assert result[0]["wanbao_selected"] == 7
    assert result[0]["wanbao_backup"] == 4
    assert result[1]["zongbao_selected"] == 0


def test_admin_shift_summary_query_excludes_future_shifts() -> None:
    class SummaryCursor:
        query = ""

        def execute(self, query: str, params: tuple[int]) -> None:
            self.query = query
            self.params = params

        def fetchall(self) -> list[dict[str, Any]]:
            return []

    cursor = SummaryCursor()

    result = db_postgres_shift_reviews.fetch_admin_shift_summaries(cursor, limit=60)

    assert result == []
    assert "s.starts_at <= CURRENT_TIMESTAMP" in cursor.query
    assert "ORDER BY s.ends_at DESC" in cursor.query
    assert "AS zongbao_selected" in cursor.query
    assert "AS zongbao_backup" in cursor.query
    assert "AS wanbao_selected" in cursor.query
    assert "AS wanbao_backup" in cursor.query
    assert "sr.admin_discarded_at IS NULL" in cursor.query


def test_preview_import_results_returns_editable_conflict_versions(
    monkeypatch,
) -> None:
    adapter = FakeAdminSummaryAdapter()
    adapter.preview_rows = [
        {
            "article_id": "article-1",
            "title": "重复新闻",
            "existing_id": "manual-1",
            "existing_summary": "管理员摘要",
            "existing_source": "管理员来源",
            "existing_status": "selected",
            "existing_report_type": "zongbao",
            "existing_version": 4,
            "duty_summary": "值班摘要",
            "duty_source": "值班来源",
            "duty_decision": "backup",
            "duty_report_type": "wanbao",
        },
        {
            "article_id": "article-2",
            "title": "新新闻",
            "existing_id": None,
        },
        {
            "article_id": "article-3",
            "title": "尚未筛选的新闻",
            "existing_id": "manual-3",
            "existing_status": "pending",
        },
    ]
    monkeypatch.setattr(admin_summary_service, "get_adapter", lambda: adapter)

    result = admin_summary_service.preview_import_results(
        shift_id="shift-1",
        article_ids=["article-1", "article-2", "article-3"],
    )

    assert result["total"] == 3
    assert result["ready_count"] == 2
    assert len(result["conflicts"]) == 1
    conflict = result["conflicts"][0]
    assert conflict["existing"]["summary"] == "管理员摘要"
    assert conflict["existing"]["manual_llm_source"] == "管理员来源"
    assert conflict["existing"]["version"] == 4
    assert conflict["duty"]["summary"] == "值班摘要"
    assert conflict["duty"]["manual_llm_source"] == "值班来源"


def test_import_results_passes_explicit_conflict_resolution(monkeypatch) -> None:
    adapter = FakeAdminSummaryAdapter()
    monkeypatch.setattr(admin_summary_service, "get_adapter", lambda: adapter)
    actor = ConsoleUser(
        method="session",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )
    resolutions = [
        {
            "article_id": "article-1",
            "choice": "existing",
            "summary": "修改后的摘要",
            "manual_llm_source": "修改后的来源",
            "existing_version": 4,
        }
    ]

    result = admin_summary_service.import_results(
        shift_id="shift-1",
        article_ids=["article-1"],
        target_status="selected",
        report_type="zongbao",
        actor=actor,
        conflict_resolutions=resolutions,
        request_id="request-1",
    )

    assert result["imported"] == 1
    assert adapter.import_query["conflict_resolutions"] == resolutions
    assert adapter.import_query["actor_user_id"] == "admin-id"


def test_admin_discarded_column_uses_shift_review_state(monkeypatch) -> None:
    adapter = FakeAdminSummaryAdapter()
    monkeypatch.setattr(admin_summary_service, "get_adapter", lambda: adapter)

    result = admin_summary_service.list_shift_results(
        shift_id="shift-1",
        decision="selected",
        report_type="zongbao",
        mismatch_only=True,
        admin_discarded_only=True,
        limit=200,
        offset=0,
    )

    assert result["total"] == 1
    assert adapter.review_query["decision"] is None
    assert adapter.review_query["report_type"] is None
    assert adapter.review_query["mismatch_only"] is False
    assert adapter.review_query["admin_discarded_only"] is True
    assert adapter.review_query["exclude_admin_discarded"] is False
    assert result["items"][0]["admin_discarded_by_display_name"] == "管理员"


def test_admin_can_discard_shift_result_without_importing_manual_review(
    monkeypatch,
) -> None:
    adapter = FakeAdminSummaryAdapter()
    monkeypatch.setattr(admin_summary_service, "get_adapter", lambda: adapter)
    actor = ConsoleUser(
        method="session",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )

    result = admin_summary_service.set_admin_discarded(
        shift_id="shift-1",
        article_id="article-1",
        discarded=True,
        actor=actor,
        request_id="request-2",
    )

    assert result["discarded"] is True
    assert adapter.import_query == {
        "shift_id": "shift-1",
        "article_id": "article-1",
        "actor_user_id": "admin-id",
        "discarded": True,
        "request_id": "request-2",
    }


def test_admin_can_discard_multiple_shift_results(
    monkeypatch,
) -> None:
    adapter = FakeAdminSummaryAdapter()
    monkeypatch.setattr(admin_summary_service, "get_adapter", lambda: adapter)
    actor = ConsoleUser(
        method="session",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )

    result = admin_summary_service.set_admin_discarded_many(
        shift_id="shift-1",
        article_ids=["article-1", "article-2", "article-1"],
        discarded=True,
        actor=actor,
        request_id="request-bulk",
    )

    assert result["updated"] == 2
    assert result["discarded"] is True
    assert adapter.bulk_discard_query == {
        "shift_id": "shift-1",
        "article_ids": ["article-1", "article-2"],
        "actor_user_id": "admin-id",
        "discarded": True,
        "request_id": "request-bulk",
    }
