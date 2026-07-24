from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import pytest

from src.console import duty_review_service
from src.console.auth_service import ConsoleUser


class FakeDutyReviewAdapter:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}
        self.ordered: dict[str, Any] = {}

    def save_shift_review(self, **kwargs: Any) -> dict[str, Any]:
        self.saved = dict(kwargs)
        return {
            "article_id": kwargs["article_id"],
            "decision": kwargs["patch"].get("decision", "pending"),
            "report_type": kwargs["patch"].get("report_type"),
            "rank": None,
            "excerpt_text": kwargs["patch"].get("excerpt_text"),
            "edited_summary": kwargs["patch"].get("edited_summary"),
            "manual_llm_source": kwargs["patch"].get("manual_llm_source"),
            "notes": kwargs["patch"].get("notes"),
            "version": 1,
            "title": "测试新闻",
            "llm_summary": "机器摘要",
            "llm_source": "测试来源",
            "score_details": {},
        }

    def update_shift_review_order(self, **kwargs: Any) -> int:
        self.ordered = dict(kwargs)
        return len(kwargs["selected_order"]) + len(kwargs["backup_order"])

    def fetch_shift_review_items(
        self,
        *,
        shift_id: str,
        decision: Optional[str],
        report_type: Optional[str],
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        del shift_id, limit, offset
        rows = [
            {
                "article_id": f"{decision}-1",
                "decision": decision,
                "report_type": report_type,
                "title": f"{decision} 新闻",
                "edited_summary": f"{decision} 摘要",
                "llm_source": "来源",
                "score_details": {},
            }
        ]
        return rows, len(rows)

    def fetch_shift_clusters(
        self,
        *,
        shift_id: str,
        report_type: str,
    ) -> list[dict[str, Any]]:
        return [
            {
                "cluster_id": f"{shift_id}-{report_type}",
                "bucket_key": "internal_positive",
                "item_ids": ["article-1", "article-2"],
            }
        ]


def _editor() -> ConsoleUser:
    return ConsoleUser(
        method="session",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )


def test_save_review_uses_authenticated_editor_id(monkeypatch) -> None:
    adapter = FakeDutyReviewAdapter()
    monkeypatch.setattr(duty_review_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        duty_review_service,
        "require_owned_shift",
        lambda *args, **kwargs: {"id": "shift-id"},
    )

    result = duty_review_service.save_review(
        shift_id="shift-id",
        article_id="article-id",
        user=_editor(),
        expected_version=0,
        patch={"decision": "selected", "report_type": "zongbao"},
    )

    assert adapter.saved["actor_user_id"] == "editor-id"
    assert result["decision"] == "selected"


def test_order_rejects_duplicate_article_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        duty_review_service,
        "require_owned_shift",
        lambda *args, **kwargs: {"id": "shift-id"},
    )

    with pytest.raises(ValueError, match="more than once"):
        duty_review_service.update_order(
            shift_id="shift-id",
            user=_editor(),
            selected_order=["article-1"],
            backup_order=["article-1"],
        )


def test_clusters_are_scoped_by_owned_shift_and_report_type(monkeypatch) -> None:
    adapter = FakeDutyReviewAdapter()
    ownership_checks: list[str] = []
    monkeypatch.setattr(duty_review_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        duty_review_service,
        "require_owned_shift",
        lambda shift_id, user: ownership_checks.append(f"{shift_id}:{user.user_id}"),
    )

    result = duty_review_service.list_clusters(
        shift_id="shift-id",
        user=_editor(),
        report_type="wanbao",
    )

    assert ownership_checks == ["shift-id:editor-id"]
    assert result["clusters"][0]["item_ids"] == ["article-1", "article-2"]
    assert result["item_total"] == 2


def test_preview_uses_independent_selected_and_backup_lists(monkeypatch) -> None:
    adapter = FakeDutyReviewAdapter()
    monkeypatch.setattr(duty_review_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        duty_review_service,
        "require_owned_shift",
        lambda *args, **kwargs: {"id": "shift-id"},
    )

    preview = duty_review_service.build_preview(
        shift_id="shift-id",
        user=_editor(),
        report_type="wanbao",
    )

    assert preview["selected_count"] == 1
    assert preview["backup_count"] == 1
    assert "—— 备选 ——" in preview["text"]
