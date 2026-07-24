from __future__ import annotations

from typing import Any

from src.console import manual_filter_admin_service
from src.console.auth_service import ConsoleUser


class FakeManualAdminAdapter:
    def __init__(self) -> None:
        self.status_update: dict[str, Any] = {}

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
