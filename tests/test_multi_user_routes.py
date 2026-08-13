from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient

from src.console import (
    admin_summary_service,
    articles_service,
    duty_review_service,
    shifts_service,
    users_service,
)
from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.manual_filter_duplicate_service import DuplicateReviewTimeoutError
from src.console.security import require_console_user


def _client_for(user: ConsoleUser) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: user
    return TestClient(app)


def _user(role: str) -> ConsoleUser:
    is_admin = role == "admin"
    return ConsoleUser(
        method="test",
        user_id="admin-id" if is_admin else "editor-id",
        username="admin" if is_admin else "editor",
        display_name="管理员" if is_admin else "值班编辑",
        role=role,
    )


def test_duty_editor_cannot_call_admin_schedule_api() -> None:
    editor = _user("duty_editor")

    response = _client_for(editor).get("/api/admin/schedules")

    assert response.status_code == 403


def test_duty_editor_cannot_delete_console_user() -> None:
    editor = _user("duty_editor")

    response = _client_for(editor).delete("/api/admin/users/another-user")

    assert response.status_code == 403


def test_duty_editor_cannot_change_admin_duty_discard_state() -> None:
    editor = _user("duty_editor")

    response = _client_for(editor).patch(
        "/api/admin/duty-summary/discard",
        json={
            "shift_id": "shift-1",
            "article_id": "article-1",
            "discarded": True,
        },
    )

    assert response.status_code == 403


def test_editor_can_refresh_shift_clusters(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}

    def list_clusters(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"clusters": [], "item_total": 0}

    monkeypatch.setattr(duty_review_service, "list_clusters", list_clusters)

    response = _client_for(editor).get(
        "/api/duty/shifts/shift-id/clusters"
        "?report_type=zongbao&force_refresh=true"
    )

    assert response.status_code == 200
    assert captured["shift_id"] == "shift-id"
    assert captured["report_type"] == "zongbao"
    assert captured["force_refresh"] is True
    assert captured["region"] is None
    assert captured["sentiment"] is None
    assert captured["limit"] is None
    assert captured["offset"] == 0
    assert captured["include_items"] is False
    assert captured["user"].user_id == "editor-id"


def test_editor_cluster_page_parameters_are_forwarded(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}

    def list_clusters(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"clusters": [], "item_total": 0}

    monkeypatch.setattr(duty_review_service, "list_clusters", list_clusters)

    response = _client_for(editor).get(
        "/api/duty/shifts/shift-id/clusters"
        "?report_type=zongbao&region=internal&sentiment=positive"
        "&limit=10&offset=20&include_items=true"
    )

    assert response.status_code == 200
    assert captured["region"] == "internal"
    assert captured["sentiment"] == "positive"
    assert captured["limit"] == 10
    assert captured["offset"] == 20
    assert captured["include_items"] is True


def test_editor_stats_report_type_is_forwarded(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}

    def get_stats(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"pending": 0}

    monkeypatch.setattr(duty_review_service, "get_stats", get_stats)

    response = _client_for(editor).get(
        "/api/duty/shifts/shift-id/stats?report_type=zongbao"
    )

    assert response.status_code == 200
    assert captured["report_type"] == "zongbao"
    assert captured["user"].user_id == "editor-id"


def test_editor_candidate_search_is_forwarded_to_backend(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}

    def list_items(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"items": [], "total": 0, "limit": 10, "offset": 0}

    monkeypatch.setattr(duty_review_service, "list_items", list_items)

    response = _client_for(editor).get(
        "/api/duty/shifts/shift-id/candidates"
        "?limit=10&offset=20&report_type=zongbao"
        "&region=internal&sentiment=positive&q=教育政策"
        "&created_before=2026-07-27"
    )

    assert response.status_code == 200
    assert captured["decision"] == "pending"
    assert captured["region"] == "internal"
    assert captured["sentiment"] == "positive"
    assert captured["query"] == "教育政策"
    assert str(captured["created_before"]) == "2026-07-27"


def test_editor_discarded_search_is_normalized_and_forwarded(monkeypatch) -> None:
    editor = _user("duty_editor")
    calls: list[dict[str, Any]] = []

    def list_items(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"items": [], "total": 0, "limit": 10, "offset": 0}

    monkeypatch.setattr(duty_review_service, "list_items", list_items)
    client = _client_for(editor)

    matched = client.get(
        "/api/duty/shifts/shift-id/reviews",
        params={"decision": "discarded", "q": "  教育政策  "},
    )
    blank = client.get(
        "/api/duty/shifts/shift-id/reviews",
        params={"decision": "discarded", "q": "   "},
    )

    assert matched.status_code == 200
    assert blank.status_code == 200
    assert calls[0]["decision"] == "discarded"
    assert calls[0]["query"] == "教育政策"
    assert calls[1]["query"] is None


def test_editor_bulk_discard_is_forwarded_once_to_owned_shift_service(
    monkeypatch,
) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}

    def bulk_discard_candidates(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {"matched": 4, "updated": 3, "skipped_finalized": 1}

    monkeypatch.setattr(
        duty_review_service,
        "bulk_discard_candidates",
        bulk_discard_candidates,
    )

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/bulk-discard",
        json={
            "region": "external",
            "sentiment": "negative",
            "q": "教育政策",
            "created_before": "2026-07-27",
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "matched": 4,
        "updated": 3,
        "skipped_finalized": 1,
    }
    assert captured["shift_id"] == "shift-id"
    assert captured["user"].user_id == "editor-id"
    assert captured["region"] == "external"
    assert captured["sentiment"] == "negative"
    assert captured["query"] == "教育政策"
    assert str(captured["created_before"]) == "2026-07-27"
    assert captured["dry_run"] is False
    assert captured["report_type"] == "zongbao"


def test_editor_bulk_discard_rejects_another_editors_shift(monkeypatch) -> None:
    editor = _user("duty_editor")

    def reject_shift(*args: Any, **kwargs: Any) -> None:
        raise shifts_service.ShiftPermissionError(
            "Duty editors can only access their own shifts"
        )

    monkeypatch.setattr(duty_review_service, "require_owned_shift", reject_shift)
    monkeypatch.setattr(
        duty_review_service,
        "get_adapter",
        lambda: (_ for _ in ()).throw(AssertionError("write must not run")),
    )

    response = _client_for(editor).post(
        "/api/duty/shifts/another-shift/bulk-discard",
        json={
            "region": "internal",
            "sentiment": "positive",
            "dry_run": False,
        },
    )

    assert response.status_code == 403


def test_bulk_discard_routes_require_region_and_sentiment() -> None:
    admin_client = _client_for(_user("admin"))
    editor_client = _client_for(_user("duty_editor"))

    for client, path in (
        (admin_client, "/api/manual_filter/bulk-discard"),
        (editor_client, "/api/duty/shifts/shift-id/bulk-discard"),
    ):
        missing_region = client.post(
            path,
            json={"sentiment": "positive", "dry_run": True},
        )
        missing_sentiment = client.post(
            path,
            json={"region": "internal", "dry_run": True},
        )

        assert missing_region.status_code == 422
        assert missing_sentiment.status_code == 422


def test_admin_can_bulk_discard_duty_results(monkeypatch) -> None:
    admin = _user("admin")
    captured: dict[str, object] = {}

    def fake_bulk_discard(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"items": [], "discarded": True, "updated": 2}

    monkeypatch.setattr(
        admin_summary_service,
        "set_admin_discarded_many",
        fake_bulk_discard,
    )

    response = _client_for(admin).patch(
        "/api/admin/duty-summary/discard-bulk",
        json={
            "shift_id": "shift-1",
            "article_ids": ["article-1", "article-2"],
            "discarded": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 2
    assert captured["article_ids"] == ["article-1", "article-2"]


def test_admin_duty_summary_forwards_process_scope(monkeypatch) -> None:
    admin = _user("admin")
    captured: dict[str, object] = {}

    def fake_list_results(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"items": [], "total": 0, "limit": 200, "offset": 0}

    monkeypatch.setattr(
        admin_summary_service,
        "list_shift_results",
        fake_list_results,
    )

    response = _client_for(admin).get(
        "/api/admin/duty-summary/shift-1/reviews"
        "?admin_unprocessed_only=true&include_admin_discarded=true"
    )

    assert response.status_code == 200
    assert captured["admin_unprocessed_only"] is True
    assert captured["include_admin_discarded"] is True


def test_admin_can_delete_console_user(monkeypatch) -> None:
    admin = _user("admin")
    captured: dict[str, str] = {}

    def delete_user(user_id: str, *, actor: ConsoleUser) -> None:
        captured["user_id"] = user_id
        captured["actor_user_id"] = actor.user_id or ""

    monkeypatch.setattr(users_service, "delete_user", delete_user)

    response = _client_for(admin).delete("/api/admin/users/editor-id")

    assert response.status_code == 200
    assert response.json()["message"] == "用户已删除，历史记录继续保留"
    assert captured == {
        "user_id": "editor-id",
        "actor_user_id": "admin-id",
    }


def test_admin_password_apis_accept_single_character(monkeypatch) -> None:
    admin = _user("admin")
    captured: dict[str, object] = {}

    def create_user(**kwargs: object) -> dict[str, object]:
        captured["created_password"] = kwargs["password"]
        return {"id": "editor-id", **kwargs}

    def reset_password(
        user_id: str,
        *,
        new_password: str,
        actor: ConsoleUser,
    ) -> None:
        captured["reset_user_id"] = user_id
        captured["reset_password"] = new_password
        captured["reset_actor_id"] = actor.user_id

    monkeypatch.setattr(users_service, "create_user", create_user)
    monkeypatch.setattr(users_service, "reset_password", reset_password)
    client = _client_for(admin)

    create_response = client.post(
        "/api/admin/users",
        json={
            "username": "editor",
            "display_name": "Editor",
            "password": "x",
            "role": "duty_editor",
            "preferred_weekday": 0,
        },
    )
    reset_response = client.post(
        "/api/admin/users/editor-id/reset-password",
        json={"new_password": "y"},
    )

    assert create_response.status_code == 201
    assert reset_response.status_code == 200
    assert captured == {
        "created_password": "x",
        "reset_user_id": "editor-id",
        "reset_password": "y",
        "reset_actor_id": "admin-id",
    }


def test_duty_editor_cannot_call_admin_manual_filter_api() -> None:
    editor = _user("duty_editor")

    response = _client_for(editor).get("/api/manual_filter/candidates")

    assert response.status_code == 403


def test_duty_editor_can_use_read_only_article_search(monkeypatch) -> None:
    editor = _user("duty_editor")
    monkeypatch.setattr(
        articles_service,
        "search_articles",
        lambda **kwargs: {
            "items": [],
            "limit": kwargs["limit"],
            "has_more": False,
            "next_cursor": None,
            "lookback_days": kwargs["lookback_days"],
            "window_start": "2026-08-12T00:00:00Z",
        },
    )

    response = _client_for(editor).get("/api/articles/search?q=教育")

    assert response.status_code == 200
    assert response.json()["has_more"] is False
    assert response.json()["lookback_days"] == 30


def test_article_search_rejects_blank_query_before_service(monkeypatch) -> None:
    editor = _user("duty_editor")

    def fail_search(**kwargs: Any) -> dict[str, Any]:
        raise AssertionError("blank searches must not reach the service")

    monkeypatch.setattr(articles_service, "search_articles", fail_search)

    response = _client_for(editor).get("/api/articles/search?q=%20%20%20")

    assert response.status_code == 422


def test_article_search_rejects_invalid_cursor() -> None:
    editor = _user("duty_editor")

    response = _client_for(editor).get(
        "/api/articles/search?q=教育&cursor=not-a-valid-cursor"
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Invalid article search cursor"


def test_admin_cannot_use_editor_shift_workspace() -> None:
    admin = _user("admin")

    response = _client_for(admin).get("/api/duty/shifts")

    assert response.status_code == 403


def test_editor_can_list_only_service_scoped_shifts(monkeypatch) -> None:
    editor = _user("duty_editor")
    monkeypatch.setattr(
        shifts_service,
        "list_user_shifts",
        lambda user: [{"id": "shift-id", "user_id": user.user_id}],
    )

    response = _client_for(editor).get("/api/duty/shifts")

    assert response.status_code == 200
    assert response.json()["items"][0]["user_id"] == "editor-id"


def test_editor_can_save_and_clear_score_feedback_in_owned_shift(monkeypatch) -> None:
    editor = _user("duty_editor")
    persisted_user_id = UUID("27b90bdd-e591-4d65-9ed9-e1614375947c")
    captured: list[tuple[str, dict[str, Any]]] = []

    def save_score_feedback(**kwargs: Any) -> dict[str, Any]:
        captured.append(("save", kwargs))
        return {
            "feedback_type": kwargs["feedback_type"],
            "score_value": 82,
            "notes": kwargs["notes"],
            "submitted_by": kwargs["user"].username,
            "submitted_by_user_id": persisted_user_id,
            "updated_at": "2026-07-28T10:00:00Z",
        }

    def clear_score_feedback(**kwargs: Any) -> bool:
        captured.append(("clear", kwargs))
        return True

    monkeypatch.setattr(
        duty_review_service,
        "save_score_feedback",
        save_score_feedback,
    )
    monkeypatch.setattr(
        duty_review_service,
        "clear_score_feedback",
        clear_score_feedback,
    )
    client = _client_for(editor)

    saved = client.put(
        "/api/duty/shifts/shift-id/score-feedback",
        json={
            "article_id": "source/item/1",
            "feedback_type": "too_low",
            "notes": "应提高分数",
        },
    )
    cleared = client.post(
        "/api/duty/shifts/shift-id/score-feedback/clear",
        json={"article_id": "source/item/1"},
    )

    assert saved.status_code == 200
    assert (
        saved.json()["score_feedback"]["submitted_by_user_id"]
        == str(persisted_user_id)
    )
    assert cleared.status_code == 200
    assert captured[0][1]["shift_id"] == "shift-id"
    assert captured[0][1]["user"].user_id == "editor-id"
    assert captured[1][1]["article_id"] == "source/item/1"


def test_stale_review_update_returns_409(monkeypatch) -> None:
    editor = _user("duty_editor")

    def conflict(**kwargs):
        raise duty_review_service.ShiftReviewConflictError("Review version is stale")

    monkeypatch.setattr(duty_review_service, "save_review", conflict)

    response = _client_for(editor).put(
        "/api/duty/shifts/shift-id/reviews/article-id",
        json={"version": 1, "decision": "selected"},
    )

    assert response.status_code == 409


def test_batch_review_edit_accepts_article_id_with_slash(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}
    article_id = "chinanews:/sh/2026/07-27/10666981"

    def save_edits(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"updated": 1, "versions": {article_id: 1}}

    monkeypatch.setattr(duty_review_service, "save_edits", save_edits)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/edit",
        json={
            "edits": {
                article_id: {
                    "summary": "人工摘要",
                    "llm_source": "工人日报",
                }
            },
            "versions": {},
        },
    )

    assert response.status_code == 200
    assert captured["edits"][article_id] == {
        "summary": "人工摘要",
        "llm_source": "工人日报",
    }
    assert response.json()["versions"] == {article_id: 1}


def test_stale_batch_review_decision_returns_409(monkeypatch) -> None:
    editor = _user("duty_editor")

    def conflict(**kwargs: Any) -> dict[str, Any]:
        raise duty_review_service.ShiftReviewConflictError("Review version is stale")

    monkeypatch.setattr(duty_review_service, "bulk_decide", conflict)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/decide",
        json={
            "selected_ids": ["article-1"],
            "versions": {"article-1": 1},
            "report_type": "zongbao",
        },
    )

    assert response.status_code == 409


def test_editor_order_forwards_review_groups(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}

    def update_order(**kwargs: Any) -> dict[str, int]:
        captured.update(kwargs)
        return {
            "selected": 1,
            "backup": 0,
            "updated": 1,
            "updated_categories": 1,
        }

    monkeypatch.setattr(duty_review_service, "update_order", update_order)

    response = _client_for(editor).put(
        "/api/duty/shifts/shift-id/order",
        json={
            "selected_order": ["article-1"],
            "backup_order": [],
            "group_orders": {"internal_positive": ["article-1"]},
        },
    )

    assert response.status_code == 200
    assert captured["group_orders"] == {"internal_positive": ["article-1"]}
    assert captured["user"].user_id == "editor-id"


def test_editor_can_finalize_and_restore_owned_shift_batch(monkeypatch) -> None:
    editor = _user("duty_editor")
    calls: list[tuple[str, dict[str, Any]]] = []

    def finalize(**kwargs: Any) -> dict[str, Any]:
        calls.append(("finalize", kwargs))
        return {
            "batch_id": "batch-1",
            "report_type": "zongbao",
            "finalized_at": "2026-07-27T10:30:00+08:00",
            "item_count": 2,
        }

    def restore(**kwargs: Any) -> dict[str, Any]:
        calls.append(("restore", kwargs))
        return {
            "batch_id": "batch-1",
            "report_type": "zongbao",
            "restored": 2,
            "article_ids": ["article-1", "article-2"],
        }

    def status(**kwargs: Any) -> dict[str, Any]:
        calls.append(("status", kwargs))
        return {
            "finalized": True,
            "report_type": "zongbao",
            "finalization": {
                "batch_id": "batch-1",
                "item_count": 2,
            },
        }

    monkeypatch.setattr(
        duty_review_service,
        "finalize_selected_batch",
        finalize,
    )
    monkeypatch.setattr(
        duty_review_service,
        "restore_finalized_batch",
        restore,
    )
    monkeypatch.setattr(
        duty_review_service,
        "get_finalization_status",
        status,
    )
    client = _client_for(editor)

    current_status = client.get(
        "/api/duty/shifts/shift-id/finalizations?report_type=zongbao"
    )
    finalized = client.post(
        "/api/duty/shifts/shift-id/finalizations",
        json={"report_type": "zongbao"},
    )
    restored = client.post(
        "/api/duty/shifts/shift-id/finalizations/batch-1/restore",
        json={},
    )
    single_item_restore = client.post(
        "/api/duty/shifts/shift-id/finalizations/batch-1/restore",
        json={"article_id": "article-1"},
    )

    assert current_status.status_code == 200
    assert current_status.json()["finalization"]["batch_id"] == "batch-1"
    assert finalized.status_code == 200
    assert finalized.json()["item_count"] == 2
    assert restored.status_code == 200
    assert restored.json()["restored"] == 2
    assert single_item_restore.status_code == 422
    assert calls[0][0] == "status"
    assert calls[0][1]["user"].user_id == "editor-id"
    assert "article_id" not in calls[2][1]
    assert [name for name, _ in calls].count("restore") == 1


def test_editor_can_check_duplicates_in_owned_shift(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}
    expected = {
        "checked_count": 2,
        "groups": [{"group_id": "duplicate-1", "items": []}],
    }

    def check_duplicates(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(duty_review_service, "check_duplicates", check_duplicates)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/duplicate-check",
        json={"report_type": "wanbao", "decision": "backup"},
    )

    assert response.status_code == 200
    assert response.json() == expected
    assert captured["shift_id"] == "shift-id"
    assert captured["user"].user_id == "editor-id"
    assert captured["report_type"] == "wanbao"
    assert captured["decision"] == "backup"


def test_duty_duplicate_check_timeout_returns_504(monkeypatch) -> None:
    editor = _user("duty_editor")

    def check_duplicates(**kwargs: Any) -> dict[str, Any]:
        raise DuplicateReviewTimeoutError("AI 查重请求超时，请稍后重试")

    monkeypatch.setattr(duty_review_service, "check_duplicates", check_duplicates)

    response = _client_for(editor).post(
        "/api/duty/shifts/shift-id/duplicate-check",
        json={"report_type": "zongbao", "decision": "selected"},
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "AI 查重请求超时，请稍后重试"


def test_single_review_route_accepts_encoded_slash_id(monkeypatch) -> None:
    editor = _user("duty_editor")
    captured: dict[str, Any] = {}

    def save_review(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "article_id": kwargs["article_id"],
            "version": 1,
        }

    monkeypatch.setattr(duty_review_service, "save_review", save_review)

    response = _client_for(editor).put(
        "/api/duty/shifts/shift-id/reviews/"
        "chinanews%3A%2Fsh%2F2026%2F07-27%2F10666981",
        json={"version": 0, "decision": "selected"},
    )

    assert response.status_code == 200
    assert captured["article_id"] == "chinanews:/sh/2026/07-27/10666981"
