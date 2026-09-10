from __future__ import annotations

from typing import Any

from src.console import manual_filter_admin_service
from src.console.auth_service import ConsoleUser


class FakeManualAdminAdapter:
    def __init__(self) -> None:
        self.status_update: dict[str, Any] = {}
        self.summary_update: dict[str, Any] = {}
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
