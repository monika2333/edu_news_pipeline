from __future__ import annotations

from typing import Any

import pytest

from src.console import manual_filter_admin_service
from src.console.auth_service import ConsoleUser


class FakeManualAdminAdapter:
    def __init__(self) -> None:
        self.status_update: dict[str, Any] = {}
        self.summary_update: dict[str, Any] = {}
        self.order_update: dict[str, Any] = {}
        self.summary_update_calls = 0
        self.rows = {
            "article-1": {
                "summary": "原摘要",
                "manual_llm_source": "原来源",
                "notes": "原备注",
                "score": 88,
            }
        }

    def update_manual_review_statuses_as_user(
        self,
        updates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.status_update = {
            "updates": updates,
            **kwargs,
        }
        return [
            {
                "article_id": item["article_id"],
                "version": kwargs["expected_versions"][item["article_id"]] + 1,
            }
            for item in updates
        ]

    def update_manual_review_summaries_as_user(
        self,
        edits: dict[str, dict[str, Any]],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.summary_update_calls += 1
        self.summary_update = {
            "edits": edits,
            **kwargs,
        }
        for article_id, edit in edits.items():
            self.rows[article_id].update(edit)
        return [
            {
                "article_id": article_id,
                "version": kwargs["expected_versions"][article_id] + 1,
            }
            for article_id in edits
        ]

    def update_manual_review_order_as_user(
        self,
        review_updates: list[dict[str, Any]],
        category_updates: list[dict[str, Any]],
        **kwargs: Any,
    ) -> tuple[int, int]:
        self.order_update = {
            "review_updates": review_updates,
            "category_updates": category_updates,
            **kwargs,
        }
        return len(review_updates), len(category_updates)


def _session_admin() -> ConsoleUser:
    return ConsoleUser(
        method="session",
        user_id="admin-user-id",
        username="admin-a",
        display_name="管理员 A",
        role="admin",
    )


def test_bulk_decide_requires_versions_and_records_real_actor(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(
        manual_filter_admin_service,
        "get_adapter",
        lambda: adapter,
    )

    result = manual_filter_admin_service.bulk_decide(
        selected_ids=["article-1"],
        backup_ids=[],
        discarded_ids=[],
        pending_ids=[],
        versions={"article-1": 7},
        actor=_session_admin(),
        request_id="request-1",
    )

    assert result["versions"] == {"article-1": 8}
    assert adapter.status_update["actor_username"] == "admin-a"
    assert adapter.status_update["actor_user_id"] == "admin-user-id"
    assert adapter.status_update["expected_versions"] == {"article-1": 7}
    assert adapter.status_update["require_versions"] is True
    assert adapter.status_update["request_id"] == "request-1"
    assert adapter.status_update["updates"][0]["rank"] is None


def test_bulk_decide_only_assigns_report_type_to_report_scoped_states(
    monkeypatch,
) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.bulk_decide(
        selected_ids=["selected-1"],
        backup_ids=["backup-1"],
        discarded_ids=["discarded-1"],
        pending_ids=["pending-1"],
        versions={
            "selected-1": 1,
            "backup-1": 1,
            "discarded-1": 1,
            "pending-1": 1,
        },
        actor=_session_admin(),
        report_type="wanbao",
    )

    updates = {
        item["article_id"]: item
        for item in adapter.status_update["updates"]
    }
    assert updates["selected-1"]["report_type"] == "wanbao"
    assert updates["backup-1"]["report_type"] == "wanbao"
    assert updates["discarded-1"]["report_type"] is None
    assert updates["pending-1"]["report_type"] is None
    assert adapter.status_update["report_type"] is None


def test_bulk_decide_resets_item_to_pending(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    result = manual_filter_admin_service.bulk_decide(
        selected_ids=[],
        backup_ids=[],
        discarded_ids=[],
        pending_ids=["article-1"],
        versions={"article-1": 5},
        actor=_session_admin(),
    )

    assert result["pending"] == 1
    assert result["versions"] == {"article-1": 6}
    update = adapter.status_update["updates"][0]
    assert update["article_id"] == "article-1"
    assert update["status"] == "pending"
    assert update["rank"] is None
    assert update["report_type"] is None


def test_save_edits_ignores_request_report_type(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.save_edits(
        {"article-1": {"summary": "编辑后摘要"}},
        versions={"article-1": 3},
        actor=_session_admin(),
        report_type="wanbao",
    )

    assert "report_type" not in adapter.summary_update["edits"]["article-1"]
    assert adapter.summary_update["report_type"] is None


def test_save_edits_summary_only_preserves_notes_and_score(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.save_edits(
        {"article-1": {"summary": "编辑后摘要"}},
        versions={"article-1": 3},
        actor=_session_admin(),
    )

    assert adapter.rows["article-1"]["summary"] == "编辑后摘要"
    assert adapter.rows["article-1"]["notes"] == "原备注"
    assert adapter.rows["article-1"]["score"] == 88


def test_save_edits_llm_source_only_preserves_summary(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.save_edits(
        {"article-1": {"llm_source": "新来源"}},
        versions={"article-1": 3},
        actor=_session_admin(),
    )

    assert adapter.rows["article-1"]["manual_llm_source"] == "新来源"
    assert adapter.rows["article-1"]["summary"] == "原摘要"


def test_save_edits_applies_multiple_submitted_fields(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.save_edits(
        {
            "article-1": {
                "summary": "编辑后摘要",
                "llm_source": "新来源",
                "notes": "新备注",
                "score": 96,
            }
        },
        versions={"article-1": 3},
        actor=_session_admin(),
    )

    assert adapter.rows["article-1"] == {
        "summary": "编辑后摘要",
        "manual_llm_source": "新来源",
        "notes": "新备注",
        "score": 96,
    }


def test_save_edits_blank_llm_source_keeps_existing_normalization(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.save_edits(
        {"article-1": {"llm_source": "  \t  "}},
        versions={"article-1": 3},
        actor=_session_admin(),
    )

    assert adapter.rows["article-1"]["manual_llm_source"] == ""


def test_save_edits_skips_payload_without_recognized_fields(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    result = manual_filter_admin_service.save_edits(
        {"article-1": {"unknown": "ignored"}},
        versions={"article-1": 3},
        actor=_session_admin(),
    )

    assert result == {"updated": 0, "versions": {}}
    assert adapter.summary_update_calls == 0
    assert adapter.rows["article-1"] == {
        "summary": "原摘要",
        "manual_llm_source": "原来源",
        "notes": "原备注",
        "score": 88,
    }


def test_archive_preserves_existing_report_type(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.archive_items(
        ["article-1"],
        versions={"article-1": 4},
        actor=_session_admin(),
        report_type="wanbao",
    )

    assert adapter.status_update["updates"][0]["status"] == "exported"
    assert adapter.status_update["updates"][0]["report_type"] is None
    assert adapter.status_update["report_type"] is None


def test_update_ranks_persists_cross_group_category_change(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    result = manual_filter_admin_service.update_ranks(
        selected_order=["article-2", "article-1"],
        backup_order=[],
        group_orders={
            "internal_positive": ["article-2"],
            "external_negative": ["article-1"],
        },
        actor=_session_admin(),
        request_id="request-ranks",
    )

    assert result == {
        "selected": 2,
        "backup": 0,
        "updated_rows": 2,
        "updated_categories": 2,
    }
    assert adapter.order_update["category_updates"] == [
        {
            "article_id": "article-2",
            "is_beijing_related": True,
            "sentiment_label": "positive",
        },
        {
            "article_id": "article-1",
            "is_beijing_related": False,
            "sentiment_label": "negative",
        },
    ]
    assert adapter.order_update["actor_username"] == "admin-a"
    assert adapter.order_update["actor_user_id"] == "admin-user-id"
    assert adapter.order_update["request_id"] == "request-ranks"


def test_update_ranks_rejects_article_in_multiple_groups(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    with pytest.raises(ValueError, match="multiple review groups"):
        manual_filter_admin_service.update_ranks(
            selected_order=["article-1"],
            backup_order=[],
            group_orders={
                "internal_positive": ["article-1"],
                "external_positive": ["article-1"],
            },
            actor=_session_admin(),
        )


def test_update_ranks_rejects_group_key_without_separator(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    with pytest.raises(
        ValueError,
        match="Invalid review group: internal",
    ) as exc_info:
        manual_filter_admin_service.update_ranks(
            selected_order=[],
            backup_order=[],
            group_orders={"internal": []},
            actor=_session_admin(),
        )

    assert str(exc_info.value) == "Invalid review group: internal"


def test_update_ranks_rejects_unknown_region(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    with pytest.raises(
        ValueError,
        match="Invalid review group: unknown_positive",
    ) as exc_info:
        manual_filter_admin_service.update_ranks(
            selected_order=[],
            backup_order=[],
            group_orders={"unknown_positive": []},
            actor=_session_admin(),
        )

    assert str(exc_info.value) == "Invalid review group: unknown_positive"


def test_update_ranks_rejects_unknown_sentiment(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    with pytest.raises(
        ValueError,
        match="Invalid review group: internal_neutral",
    ) as exc_info:
        manual_filter_admin_service.update_ranks(
            selected_order=[],
            backup_order=[],
            group_orders={"internal_neutral": []},
            actor=_session_admin(),
        )

    assert str(exc_info.value) == "Invalid review group: internal_neutral"


def test_update_ranks_rejects_selected_backup_overlap(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    with pytest.raises(ValueError, match="more than one decision group"):
        manual_filter_admin_service.update_ranks(
            selected_order=["article-1"],
            backup_order=["article-1"],
            group_orders={},
            actor=_session_admin(),
        )


def test_update_ranks_rejects_grouped_article_missing_from_order(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    with pytest.raises(ValueError, match="missing from review order"):
        manual_filter_admin_service.update_ranks(
            selected_order=["article-1"],
            backup_order=[],
            group_orders={"internal_positive": ["article-2"]},
            actor=_session_admin(),
        )


def test_update_ranks_assigns_independent_one_based_ranks(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    manual_filter_admin_service.update_ranks(
        selected_order=["selected-1", "selected-2"],
        backup_order=["backup-1", "backup-2"],
        group_orders={},
        actor=_session_admin(),
    )

    assert adapter.order_update["review_updates"] == [
        {
            "article_id": "selected-1",
            "status": "selected",
            "rank": 1.0,
            "report_type": "zongbao",
        },
        {
            "article_id": "selected-2",
            "status": "selected",
            "rank": 2.0,
            "report_type": "zongbao",
        },
        {
            "article_id": "backup-1",
            "status": "backup",
            "rank": 1.0,
            "report_type": "zongbao",
        },
        {
            "article_id": "backup-2",
            "status": "backup",
            "rank": 2.0,
            "report_type": "zongbao",
        },
    ]


def test_update_ranks_empty_orders_skip_adapter_write(monkeypatch) -> None:
    adapter = FakeManualAdminAdapter()
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    result = manual_filter_admin_service.update_ranks(
        selected_order=[],
        backup_order=[],
        group_orders={},
        actor=_session_admin(),
    )

    assert result == {
        "selected": 0,
        "backup": 0,
        "updated_rows": 0,
        "updated_categories": 0,
    }
    assert adapter.order_update == {}


def test_clear_review_buckets_counts_successful_rows_and_preserves_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    rows = [
        {
            "article_id": "selected-null-type",
            "status": "discarded",
            "previous_status": "selected",
            "rank": None,
            "report_type": None,
            "summary": "摘要一",
            "manual_llm_source": "来源一",
            "notes": "备注一",
            "score": 91,
            "decided_by": "system:scheduled_clear",
            "decided_by_user_id": None,
        },
        {
            "article_id": "backup-wanbao",
            "status": "discarded",
            "previous_status": "backup",
            "rank": None,
            "report_type": "wanbao",
            "summary": "摘要二",
            "manual_llm_source": "来源二",
            "notes": "备注二",
            "score": 83,
            "decided_by": "system:scheduled_clear",
            "decided_by_user_id": None,
        },
    ]

    class ClearAdapter:
        def clear_review_buckets_as_user(self, **kwargs: Any) -> list[dict[str, Any]]:
            calls.append(kwargs)
            return rows

    monkeypatch.setattr(
        manual_filter_admin_service,
        "get_adapter",
        lambda: ClearAdapter(),
    )

    result = manual_filter_admin_service.clear_review_buckets(
        actor_username="system:scheduled_clear",
        actor_user_id=None,
        trigger="scheduled",
    )

    assert result == {
        "total": 2,
        "buckets": {
            "zongbao": {"selected": 1, "backup": 0},
            "wanbao": {"selected": 0, "backup": 1},
        },
    }
    assert calls == [
        {
            "actor_username": "system:scheduled_clear",
            "actor_user_id": None,
            "trigger": "scheduled",
            "request_id": None,
        }
    ]
    assert rows[0]["report_type"] is None
    assert rows[0]["summary"] == "摘要一"
    assert rows[0]["manual_llm_source"] == "来源一"
    assert rows[0]["notes"] == "备注一"
    assert rows[0]["score"] == 91
    assert all(row["status"] == "discarded" for row in rows)
    assert all(row["rank"] is None for row in rows)
    assert all(row["decided_by_user_id"] is None for row in rows)
