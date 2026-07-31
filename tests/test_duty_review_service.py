from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import pytest

from src.console import duty_review_service
from src.console.auth_service import ConsoleUser


class FakeDutyReviewAdapter:
    def __init__(self) -> None:
        self.saved: dict[str, Any] = {}
        self.saved_batch: dict[str, Any] = {}
        self.ordered: dict[str, Any] = {}
        self.fetch_scopes: list[tuple[Optional[str], bool]] = []
        self.fetch_kwargs: list[dict[str, Any]] = []
        self.fetch_article_ids: list[Optional[list[str]]] = []
        self.finalized: dict[str, Any] = {}
        self.restored: dict[str, Any] = {}

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

    def save_shift_reviews(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.saved_batch = dict(kwargs)
        return [
            {
                "article_id": update["article_id"],
                "version": int(update.get("expected_version") or 0) + 1,
            }
            for update in kwargs["updates"]
        ]

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
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        query: Optional[str] = None,
        published_before: object = None,
        article_ids: Optional[Sequence[str]] = None,
        exclude_finalized: bool = False,
        hide_submitted: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        del shift_id, limit, offset
        self.fetch_scopes.append((decision, exclude_finalized))
        normalized_article_ids = (
            [str(article_id) for article_id in article_ids]
            if article_ids is not None
            else None
        )
        self.fetch_article_ids.append(normalized_article_ids)
        self.fetch_kwargs.append(
            {
                "region": region,
                "sentiment": sentiment,
                "query": query,
                "published_before": published_before,
                "hide_submitted": hide_submitted,
            }
        )
        row_ids = normalized_article_ids or [f"{decision}-1"]
        rows = [
            {
                "article_id": article_id,
                "decision": decision,
                "report_type": report_type,
                "title": f"{decision} 新闻",
                "edited_summary": f"{decision} 摘要",
                "llm_source": "来源",
                "score_details": {},
            }
            for article_id in row_ids
        ]
        return rows, len(rows)

    def fetch_shift_stats(
        self,
        shift_id: str,
        *,
        report_type: Optional[str] = None,
    ) -> dict[str, Any]:
        return {
            "shift_id": shift_id,
            "report_type": report_type,
            "pending": 4,
            "selected": 2,
            "backup": 1,
            "discarded": 3,
        }

    def finalize_shift_review_batch(self, **kwargs: Any) -> dict[str, Any]:
        self.finalized = dict(kwargs)
        return {
            "id": "batch-1",
            "report_type": kwargs["report_type"],
            "finalized_at": "2026-07-27T10:30:00+08:00",
            "item_count": 2,
        }

    def fetch_shift_finalized_items(self, **kwargs: Any) -> list[dict[str, Any]]:
        del kwargs
        return [
            {
                "article_id": f"article-{rank}",
                "decision": "selected",
                "report_type": "zongbao",
                "title": f"第 {rank} 条",
                "score_details": {},
                "finalized_batch_id": "batch-1",
                "finalized_rank": rank,
                "finalized_at": "2026-07-27T10:30:00+08:00",
                "finalized_by_display_name": "值班编辑",
            }
            for rank in (1, 2)
        ]

    def fetch_shift_finalization_status(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {
            "batch_id": "batch-1",
            "report_type": kwargs["report_type"],
            "finalized_at": "2026-07-27T10:30:00+08:00",
            "finalized_by_display_name": "值班编辑",
            "item_count": 2,
        }

    def restore_shift_review_finalization(self, **kwargs: Any) -> dict[str, Any]:
        self.restored = dict(kwargs)
        return {
            "batch_id": kwargs["batch_id"],
            "report_type": "zongbao",
            "restored": 2,
            "article_ids": ["article-1", "article-2"],
        }

    def fetch_shift_clusters(
        self,
        *,
        shift_id: str,
        report_type: str,
        hide_submitted: bool = False,
    ) -> list[dict[str, Any]]:
        item_ids = ["article-1"] if hide_submitted else ["article-1", "article-2"]
        return [
            {
                "cluster_id": f"{shift_id}-{report_type}",
                "bucket_key": "internal_positive",
                "item_ids": item_ids,
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


@pytest.fixture
def fake_adapter(monkeypatch: pytest.MonkeyPatch) -> FakeDutyReviewAdapter:
    adapter = FakeDutyReviewAdapter()
    monkeypatch.setattr(duty_review_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        duty_review_service,
        "require_owned_shift",
        lambda *args, **kwargs: {"id": "shift-id"},
    )
    return adapter


def test_save_review_uses_authenticated_editor_id(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:

    result = duty_review_service.save_review(
        shift_id="shift-id",
        article_id="article-id",
        user=_editor(),
        expected_version=0,
        patch={"decision": "selected", "report_type": "zongbao"},
    )

    assert fake_adapter.saved["actor_user_id"] == "editor-id"
    assert result["decision"] == "selected"


def test_score_feedback_is_scoped_to_owned_shift(
    fake_adapter: FakeDutyReviewAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def save_score_feedback(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"feedback_type": kwargs["feedback_type"]}

    monkeypatch.setattr(
        duty_review_service.score_feedback_service,
        "save_score_feedback",
        save_score_feedback,
    )

    result = duty_review_service.save_score_feedback(
        shift_id="shift-id",
        article_id=" article-1 ",
        feedback_type="too_high",
        notes="偏高",
        user=_editor(),
    )

    assert result["feedback_type"] == "too_high"
    assert fake_adapter.fetch_article_ids[-1] == ["article-1"]
    assert captured["article_id"] == "article-1"
    assert captured["actor"].user_id == "editor-id"


def test_score_feedback_rejects_article_outside_shift(
    fake_adapter: FakeDutyReviewAdapter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        fake_adapter,
        "fetch_shift_review_items",
        lambda **kwargs: ([], 0),
    )

    with pytest.raises(
        duty_review_service.ShiftReviewArticleNotFoundError,
        match="not available",
    ):
        duty_review_service.save_score_feedback(
            shift_id="shift-id",
            article_id="outside-article",
            feedback_type="too_low",
            notes=None,
            user=_editor(),
        )


def test_save_edits_batches_article_ids_with_slashes(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    article_id = "chinanews:/sh/2026/07-27/10666981"

    result = duty_review_service.save_edits(
        shift_id="shift-id",
        user=_editor(),
        edits={
            article_id: {
                "summary": "人工摘要",
                "llm_source": "工人日报",
            }
        },
        versions={article_id: 3},
    )

    update = fake_adapter.saved_batch["updates"][0]
    assert fake_adapter.saved_batch["action"] == "shift_review.edit"
    assert update["article_id"] == article_id
    assert update["expected_version"] == 3
    assert update["patch"] == {
        "edited_summary": "人工摘要",
        "manual_llm_source": "工人日报",
    }
    assert result["versions"] == {article_id: 4}


def test_bulk_decide_sends_one_batch_with_all_versions(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    slash_id = "chinanews:/sh/2026/07-27/10666981"

    result = duty_review_service.bulk_decide(
        shift_id="shift-id",
        user=_editor(),
        selected_ids=["article-1", slash_id],
        backup_ids=[],
        discarded_ids=[],
        pending_ids=[],
        versions={"article-1": 2, slash_id: 1},
        report_type="zongbao",
    )

    updates = fake_adapter.saved_batch["updates"]
    assert fake_adapter.saved_batch["action"] == "shift_review.decide"
    assert [update["article_id"] for update in updates] == ["article-1", slash_id]
    assert [update["expected_version"] for update in updates] == [2, 1]
    assert all(
        update["patch"] == {
            "decision": "selected",
            "report_type": "zongbao",
        }
        for update in updates
    )
    assert result["selected"] == 2
    assert result["versions"] == {"article-1": 3, slash_id: 2}


def test_serialize_review_item_preserves_admin_comparison_state() -> None:
    result = duty_review_service.serialize_review_item(
        {
            "article_id": "article-id",
            "decision": "backup",
            "report_type": "wanbao",
            "version": 2,
            "title": "测试新闻",
            "llm_summary": "机器摘要",
            "content_markdown": "不应返回浏览器的正文",
            "score_details": {},
            "admin_status": "selected",
            "admin_report_type": "zongbao",
            "admin_decided_by": "admin-a",
            "admin_version": 5,
            "finalized_batch_id": "batch-1",
            "finalized_rank": 2,
            "finalized_at": "2026-07-27T10:30:00+08:00",
        },
        fallback_report_type="wanbao",
    )

    assert result["decision"] == "backup"
    assert result["admin_status"] == "selected"
    assert result["admin_report_type"] == "zongbao"
    assert result["admin_decided_by"] == "admin-a"
    assert result["admin_version"] == 5
    assert result["finalized_batch_id"] == "batch-1"
    assert result["finalized_rank"] == 2
    assert result["finalized_at"] == "2026-07-27T10:30:00+08:00"
    assert "content_markdown" not in result


def test_candidate_search_filters_are_forwarded_to_database(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    duty_review_service.list_items(
        shift_id="shift-id",
        user=_editor(),
        decision="pending",
        report_type="zongbao",
        limit=10,
        offset=0,
        region="internal",
        sentiment="positive",
        query="  教育政策  ",
    )

    assert fake_adapter.fetch_kwargs == [
        {
            "region": "internal",
            "sentiment": "positive",
            "query": "教育政策",
            "published_before": None,
            "hide_submitted": False,
        }
    ]


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


def test_order_persists_review_groups_with_rank_updates(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    result = duty_review_service.update_order(
        shift_id="shift-id",
        user=_editor(),
        selected_order=["article-1"],
        backup_order=["article-2"],
        group_orders={
            "internal_positive": ["article-1"],
            "external_negative": ["article-2"],
        },
    )

    assert fake_adapter.ordered["category_updates"] == [
        {
            "article_id": "article-1",
            "is_beijing_related": True,
            "sentiment_label": "positive",
        },
        {
            "article_id": "article-2",
            "is_beijing_related": False,
            "sentiment_label": "negative",
        },
    ]
    assert result["updated_categories"] == 2


def test_order_rejects_grouped_article_missing_from_review_order(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    with pytest.raises(ValueError, match="missing from review order"):
        duty_review_service.update_order(
            shift_id="shift-id",
            user=_editor(),
            selected_order=["article-1"],
            backup_order=[],
            group_orders={"internal_positive": ["article-2"]},
        )

    assert fake_adapter.ordered == {}


def test_clusters_are_scoped_by_owned_shift_and_report_type(monkeypatch) -> None:
    adapter = FakeDutyReviewAdapter()
    ownership_checks: list[str] = []
    refreshes: list[dict[str, Any]] = []
    monkeypatch.setattr(duty_review_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        duty_review_service,
        "require_owned_shift",
        lambda shift_id, user: ownership_checks.append(f"{shift_id}:{user.user_id}"),
    )
    monkeypatch.setattr(
        duty_review_service.manual_filter_cluster,
        "refresh_clusters",
        lambda **kwargs: refreshes.append(dict(kwargs)) or True,
    )

    result = duty_review_service.list_clusters(
        shift_id="shift-id",
        user=_editor(),
        report_type="wanbao",
        force_refresh=True,
    )

    assert ownership_checks == ["shift-id:editor-id"]
    assert refreshes == [{}]
    assert result["clusters"][0]["item_ids"] == ["article-1", "article-2"]
    assert result["item_total"] == 2


def test_cluster_page_loads_only_current_bucket_items(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    result = duty_review_service.list_clusters(
        shift_id="shift-id",
        user=_editor(),
        report_type="zongbao",
        region="internal",
        sentiment="positive",
        limit=1,
        offset=0,
        include_items=True,
    )

    assert result["total"] == 1
    assert result["item_total"] == 2
    assert result["limit"] == 1
    assert fake_adapter.fetch_article_ids == [["article-1", "article-2"]]
    assert [
        item["article_id"]
        for item in result["clusters"][0]["items"]
    ] == ["article-1", "article-2"]


def test_cluster_page_hides_submitted_items_without_flattening_groups(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    result = duty_review_service.list_clusters(
        shift_id="shift-id",
        user=_editor(),
        report_type="zongbao",
        region="internal",
        sentiment="positive",
        limit=10,
        offset=0,
        include_items=True,
        hide_submitted=True,
    )

    assert result["total"] == 1
    assert result["item_total"] == 1
    assert result["clusters"][0]["cluster_id"] == "shift-id-zongbao"
    assert result["clusters"][0]["size"] == 1
    assert fake_adapter.fetch_article_ids == [["article-1"]]
    assert fake_adapter.fetch_kwargs[-1]["hide_submitted"] is True


def test_stats_are_aggregated_for_requested_report_type(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    result = duty_review_service.get_stats(
        shift_id="shift-id",
        user=_editor(),
        report_type="zongbao",
    )

    assert result["report_type"] == "zongbao"
    assert result["pending"] == 4


def test_duplicate_check_loads_only_owned_shift_review_items(monkeypatch) -> None:
    adapter = FakeDutyReviewAdapter()
    ownership_checks: list[str] = []
    captured: dict[str, Any] = {}
    monkeypatch.setattr(duty_review_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        duty_review_service,
        "require_owned_shift",
        lambda shift_id, user: ownership_checks.append(f"{shift_id}:{user.user_id}"),
    )

    def fake_check_duplicates(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        review = kwargs["review_loader"](
            "backup",
            limit=200,
            offset=0,
            report_type="wanbao",
        )
        return {"checked_count": review["total"], "groups": []}

    monkeypatch.setattr(
        duty_review_service.manual_filter_duplicate_service,
        "check_duplicates",
        fake_check_duplicates,
    )

    result = duty_review_service.check_duplicates(
        shift_id="shift-id",
        user=_editor(),
        report_type="wanbao",
        decision="backup",
    )

    assert captured["report_type"] == "wanbao"
    assert captured["decision"] == "backup"
    assert ownership_checks == ["shift-id:editor-id"]
    assert result == {"checked_count": 1, "groups": []}


def test_preview_uses_independent_selected_and_backup_lists(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:

    preview = duty_review_service.build_preview(
        shift_id="shift-id",
        user=_editor(),
        report_type="wanbao",
    )

    assert preview["selected_count"] == 1
    assert preview["backup_count"] == 1
    assert "—— 备选 ——" in preview["text"]
    assert fake_adapter.fetch_scopes == [
        ("selected", True),
        ("backup", False),
    ]


def test_selected_list_excludes_previously_finalized_items(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:

    duty_review_service.list_items(
        shift_id="shift-id",
        user=_editor(),
        decision="selected",
        report_type="zongbao",
        limit=200,
        offset=0,
    )

    assert fake_adapter.fetch_scopes == [("selected", True)]


def test_finalize_selected_batch_uses_editor_without_changing_decision(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    result = duty_review_service.finalize_selected_batch(
        shift_id="shift-id",
        user=_editor(),
        report_type="zongbao",
        request_id="request-1",
    )

    assert fake_adapter.finalized == {
        "shift_id": "shift-id",
        "report_type": "zongbao",
        "actor_user_id": "editor-id",
        "request_id": "request-1",
    }
    assert result["batch_id"] == "batch-1"
    assert result["item_count"] == 2


def test_finalization_status_returns_single_current_finalization(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    result = duty_review_service.get_finalization_status(
        shift_id="shift-id",
        user=_editor(),
        report_type="zongbao",
    )

    assert result["finalized"] is True
    assert result["finalization"]["batch_id"] == "batch-1"
    assert result["finalization"]["item_count"] == 2
    assert "items" not in result["finalization"]


def test_restore_finalized_batch_returns_all_items_to_current_batch(
    fake_adapter: FakeDutyReviewAdapter,
) -> None:
    result = duty_review_service.restore_finalized_batch(
        shift_id="shift-id",
        batch_id="batch-1",
        user=_editor(),
        request_id="request-2",
    )

    assert fake_adapter.restored == {
        "shift_id": "shift-id",
        "batch_id": "batch-1",
        "actor_user_id": "editor-id",
        "request_id": "request-2",
    }
    assert result["restored"] == 2
    assert result["article_ids"] == ["article-1", "article-2"]
