from __future__ import annotations

from collections.abc import Callable
from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from src.console import submission_archive_routes, submission_archive_service
from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user
from src.console.submission_archive_schemas import (
    CreateSubmissionReportRequest,
)


def _admin() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="admin-id",
        username="admin",
        display_name="管理员",
        role="admin",
    )


def _editor() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="editor-id",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )


def _client(user_factory: Callable[[], ConsoleUser]) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_console_user] = user_factory
    return TestClient(app)


def test_admin_can_parse_report_without_writing_database() -> None:
    response = _client(_admin).post(
        "/api/submission-archive/parse",
        json={
            "pasted_text": (
                "首都教育舆情\n总第1期\n2026年7月28日\n"
                "【舆情速览】\n一、测试条目\n正文（北京日报）"
            )
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["detected_report_type"] == "wanbao"
    assert payload["items"][0]["source"] == "北京日报"


def test_duty_editor_cannot_use_report_import_api() -> None:
    response = _client(_editor).post(
        "/api/submission-archive/parse",
        json={"pasted_text": "任意文本"},
    )

    assert response.status_code == 403


def test_create_report_api_returns_before_link_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched_report_ids: list[str] = []

    def fake_create_report(**_kwargs: object) -> dict[str, object]:
        return {
            "report": {"id": "report-id", "items": []},
            "link_summary": {"processing": 1},
        }

    monkeypatch.setattr(
        submission_archive_service,
        "create_report",
        fake_create_report,
    )
    monkeypatch.setattr(
        submission_archive_routes,
        "launch_submission_report_processing",
        launched_report_ids.append,
    )
    request = CreateSubmissionReportRequest(
        report_type="zongbao",
        report_date=date(2026, 7, 29),
        compiled_date=date(2026, 7, 29),
        pasted_text="原始全文",
        items=[{"title": "测试条目"}],
    )

    result = submission_archive_routes.create_report_api(
        request,
        _admin(),
    )

    assert result["report"]["id"] == "report-id"
    assert launched_report_ids == ["report-id"]


def test_create_report_conflict_serializes_database_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report_id = UUID("f4054ecc-f97a-443c-bd41-90b0a9f8edbb")

    def fake_create_report(**_kwargs: object) -> dict[str, object]:
        raise submission_archive_service.SubmissionReportConflictError(
            {
                "id": report_id,
                "report_type": "wanbao",
                "report_date": date(2026, 7, 23),
                "title_line": "首都教育舆情",
                "item_count": 12,
            }
        )

    monkeypatch.setattr(
        submission_archive_service,
        "create_report",
        fake_create_report,
    )

    response = _client(_admin).post(
        "/api/submission-archive/reports",
        json={
            "report_type": "wanbao",
            "report_date": "2026-07-23",
            "compiled_date": "2026-07-23",
            "pasted_text": "原始全文",
            "items": [{"title": "测试条目"}],
        },
    )

    assert response.status_code == 409
    existing = response.json()["detail"]["existing_report"]
    assert existing["id"] == str(report_id)
    assert existing["report_date"] == "2026-07-23"
    assert existing["item_count"] == 12


def test_link_candidates_api_uses_contract_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_search_link_candidates(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "item": {"id": "item-1"},
            "items": [],
            "window_days": 15,
            "window_start": date(2026, 7, 25),
            "window_end": date(2026, 8, 24),
            "has_more": False,
        }

    monkeypatch.setattr(
        submission_archive_service,
        "search_link_candidates",
        fake_search_link_candidates,
    )

    response = _client(_editor).get(
        "/api/submission-archive/items/item-1/link-candidates",
        params={"q": "招生"},
    )

    assert response.status_code == 200
    assert captured == {
        "item_id": "item-1",
        "query": "招生",
        "window_days": 15,
        "limit": 20,
        "offset": 0,
    }
    assert response.json()["window_start"] == "2026-07-25"
    assert "total" not in response.json()


def test_link_candidates_api_rejects_blank_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        submission_archive_service,
        "search_link_candidates",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("q 不能为空")),
    )

    response = _client(_editor).get(
        "/api/submission-archive/items/item-1/link-candidates",
        params={"q": "   "},
    )

    assert response.status_code == 422


def test_manual_link_and_unlink_routes_return_updated_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    linked = {"article_id": "article-1", "link_status": "matched"}
    unlinked = {"article_id": None, "link_status": "unmatched"}
    monkeypatch.setattr(
        submission_archive_service,
        "manual_link_item",
        lambda **_kwargs: linked,
    )
    monkeypatch.setattr(
        submission_archive_service,
        "manual_unlink_item",
        lambda **_kwargs: unlinked,
    )
    client = _client(_editor)

    link_response = client.post(
        "/api/submission-archive/items/item-1/manual-link",
        json={"article_id": "article-1"},
    )
    unlink_response = client.delete(
        "/api/submission-archive/items/item-1/manual-link"
    )

    assert link_response.status_code == 200
    assert link_response.json() == linked
    assert unlink_response.status_code == 200
    assert unlink_response.json() == unlinked


def test_manual_link_processing_conflict_returns_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_processing(**_kwargs: object) -> dict[str, object]:
        raise submission_archive_service.SubmissionLinkProcessingError(
            "正在处理"
        )

    monkeypatch.setattr(
        submission_archive_service,
        "manual_link_item",
        raise_processing,
    )

    response = _client(_editor).post(
        "/api/submission-archive/items/item-1/manual-link",
        json={"article_id": "article-1"},
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (
            submission_archive_service.SubmissionReportNotFoundError(
                "未找到这个存档条目"
            ),
            404,
        ),
        (ValueError("article_id 在 news_summaries 中不存在"), 422),
    ],
)
def test_manual_link_maps_not_found_errors(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
) -> None:
    def raise_error(**_kwargs: object) -> dict[str, object]:
        raise error

    monkeypatch.setattr(
        submission_archive_service,
        "manual_link_item",
        raise_error,
    )

    response = _client(_editor).post(
        "/api/submission-archive/items/item-1/manual-link",
        json={"article_id": "article-1"},
    )

    assert response.status_code == expected_status


def test_fetch_duplicate_details_accepts_article_id_with_slashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    article_id = "chinanews:/ty/2026/08-05/10672568"

    def fake_fetch_duplicate_details(value: str) -> dict[str, object]:
        captured["article_id"] = value
        return {"matches": [{"title": "条目", "body": "报送稿正文"}]}

    monkeypatch.setattr(
        submission_archive_service,
        "fetch_duplicate_details",
        fake_fetch_duplicate_details,
    )

    response = _client(_editor).get(
        "/api/submission-archive/duplicates/"
        "chinanews%3A%2Fty%2F2026%2F08-05%2F10672568"
    )

    assert response.status_code == 200
    assert response.json() == {"matches": [{"title": "条目", "body": "报送稿正文"}]}
    assert captured["article_id"] == article_id


def test_dismiss_duplicates_accepts_article_id_with_slashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    article_id = "chinanews:/ty/2026/08-05/10672568"

    def fake_dismiss_duplicates(**kwargs: object) -> int:
        captured.update(kwargs)
        return 1

    monkeypatch.setattr(
        submission_archive_service,
        "dismiss_duplicates",
        fake_dismiss_duplicates,
    )

    response = _client(_editor).post(
        "/api/submission-archive/duplicates/"
        "chinanews%3A%2Fty%2F2026%2F08-05%2F10672568/dismiss"
    )

    assert response.status_code == 200
    assert response.json() == {"dismissed": 1}
    assert captured["article_id"] == article_id
    assert captured["user"] == _editor()


def test_update_item_route_returns_updated_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    updated = {"id": "item-1", "title": "新标题", "source": "北京日报"}

    def fake_update_item_fields(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return updated

    monkeypatch.setattr(
        submission_archive_service,
        "update_item_fields",
        fake_update_item_fields,
    )

    response = _client(_admin).patch(
        "/api/submission-archive/items/item-1",
        json={
            "title": "新标题",
            "body": "新正文",
            "source": "北京日报",
            "urls": ["https://example.com/a"],
        },
    )

    assert response.status_code == 200
    assert response.json() == updated
    assert captured == {
        "item_id": "item-1",
        "title": "新标题",
        "body": "新正文",
        "source": "北京日报",
        "urls": ["https://example.com/a"],
    }


def test_duty_editor_cannot_update_item() -> None:
    response = _client(_editor).patch(
        "/api/submission-archive/items/item-1",
        json={"title": "新标题"},
    )

    assert response.status_code == 403
