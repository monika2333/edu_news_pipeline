from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user


class FakeManualFilterAdapter:
    def __init__(self, rows: list[Dict[str, Any]]) -> None:
        self.rows = rows
        for row in self.rows:
            if not row.get("report_type"):
                row["report_type"] = "zongbao"

    @staticmethod
    def _normalized_report_type(value: Optional[str]) -> str:
        normalized = (value or "zongbao").strip().lower()
        return normalized if normalized in ("zongbao", "wanbao") else "zongbao"

    @staticmethod
    def _published_local_date(row: Mapping[str, Any]) -> Optional[date]:
        publish_time_iso = row.get("publish_time_iso")
        if publish_time_iso:
            try:
                value = str(publish_time_iso).replace("Z", "+00:00")
                return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).date()
            except ValueError:
                return None
        publish_time = row.get("publish_time")
        if publish_time is None:
            return None
        try:
            return datetime.fromtimestamp(float(publish_time), tz=ZoneInfo("Asia/Shanghai")).date()
        except (TypeError, ValueError, OSError):
            return None

    def fetch_manual_reviews(
        self,
        *,
        status: str,
        limit: int,
        offset: int,
        only_ready: bool = False,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        report_type: Optional[str] = None,
        order_by_decided_at: bool = False,
    ) -> Tuple[list[Dict[str, Any]], int]:
        target_type = self._normalized_report_type(report_type)
        filtered = [
            row
            for row in self.rows
            if row.get("status") == status and self._normalized_report_type(row.get("report_type")) == target_type
        ]
        if only_ready:
            filtered = [row for row in filtered if row.get("news_status") == "ready_for_export"]
        if region in ("internal", "external"):
            target = region == "internal"
            filtered = [row for row in filtered if row.get("is_beijing_related") is target]
        if sentiment in ("positive", "negative"):
            filtered = [row for row in filtered if (row.get("sentiment_label") or "").lower() == sentiment]
        filtered.sort(
            key=lambda row: (
                row.get("rank") is None,
                row.get("rank") or 0,
                -(row.get("score") or 0),
                row.get("publish_time_iso") or "",
                row.get("article_id") or "",
            )
        )
        total = len(filtered)
        return filtered[offset : offset + limit], total

    def search_manual_candidates(
        self,
        *,
        query: Optional[str] = None,
        published_before: Optional[date] = None,
        limit: int,
        offset: int,
        region: Optional[str] = None,
        sentiment: Optional[str] = None,
        report_type: Optional[str] = None,
    ) -> Tuple[list[Dict[str, Any]], int]:
        rows, _ = self.fetch_manual_reviews(
            status="pending",
            limit=10_000,
            offset=0,
            only_ready=True,
            region=region,
            sentiment=sentiment,
            report_type=report_type,
        )
        normalized_query = (query or "").strip().lower()
        filtered = list(rows)
        if normalized_query:
            filtered = [
                row
                for row in filtered
                if normalized_query in " ".join(
                    [
                        str(row.get("title") or "").lower(),
                        str(row.get("llm_summary") or "").lower(),
                        str(row.get("content_markdown") or "").lower(),
                    ]
                )
            ]
        if published_before:
            filtered = [
                row
                for row in filtered
                if self._published_local_date(row) is not None and self._published_local_date(row) < published_before
            ]
        total = len(filtered)
        return filtered[offset : offset + limit], total

    def count_manual_candidates_before_date(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str] = None,
        published_before: Optional[date] = None,
        report_type: Optional[str] = None,
    ) -> int:
        _, total = self.search_manual_candidates(
            query=query,
            published_before=published_before,
            limit=10_000,
            offset=0,
            region=region,
            sentiment=sentiment,
            report_type=report_type,
        )
        return total

    def discard_manual_candidates_before_date(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str] = None,
        published_before: Optional[date] = None,
        actor: Optional[str] = None,
        decided_at: Optional[Any] = None,
        report_type: Optional[str] = None,
    ) -> int:
        rows, _ = self.search_manual_candidates(
            query=query,
            published_before=published_before,
            limit=10_000,
            offset=0,
            region=region,
            sentiment=sentiment,
            report_type=report_type,
        )
        updated = 0
        for item in rows:
            for row in self.rows:
                if row.get("article_id") != item.get("article_id"):
                    continue
                row["status"] = "discarded"
                row["decided_by"] = actor
                row["decided_at"] = decided_at
                updated += 1
                break
        return updated

    def discard_manual_candidates_before_date_as_user(
        self,
        *,
        region: str,
        sentiment: str,
        query: Optional[str],
        published_before: Optional[date],
        report_type: str,
        actor_username: str,
        actor_user_id: Optional[str],
        request_id: Optional[str],
    ) -> list[Dict[str, Any]]:
        del actor_user_id, request_id
        rows, _ = self.search_manual_candidates(
            query=query,
            published_before=published_before,
            limit=10_000,
            offset=0,
            region=region,
            sentiment=sentiment,
            report_type=report_type,
        )
        for row in rows:
            row["status"] = "discarded"
            row["decided_by"] = actor_username
            row["version"] = int(row.get("version") or 1) + 1
        return rows


def _build_rows() -> list[Dict[str, Any]]:
    return [
        {
            "article_id": "a1",
            "title": "学科建设大会举行",
            "llm_summary": "大会总结",
            "manual_summary": None,
            "manual_llm_source": None,
            "rank": None,
            "score": 90,
            "news_status": "ready_for_export",
            "status": "pending",
            "source": "src",
            "publish_time_iso": "2025-01-01T00:00:00Z",
            "publish_time": None,
            "sentiment_label": "positive",
            "sentiment_confidence": 0.9,
            "is_beijing_related": True,
            "external_importance_score": 80,
            "decided_by": None,
            "decided_at": None,
            "content_markdown": "学科建设大会内容",
            "url": "http://example.com/a1",
            "score_details": {"matched_rules": []},
            "report_type": "zongbao",
        },
        {
            "article_id": "a2",
            "title": "其他新闻",
            "llm_summary": "普通摘要",
            "manual_summary": None,
            "manual_llm_source": None,
            "rank": None,
            "score": 60,
            "news_status": "ready_for_export",
            "status": "pending",
            "source": "src2",
            "publish_time_iso": "2025-01-05T00:00:00Z",
            "publish_time": None,
            "sentiment_label": "positive",
            "sentiment_confidence": 0.8,
            "is_beijing_related": True,
            "external_importance_score": 40,
            "decided_by": None,
            "decided_at": None,
            "content_markdown": "其他内容",
            "url": "http://example.com/a2",
            "score_details": {"matched_rules": []},
            "report_type": "zongbao",
        },
    ]


def _anonymous_console_user() -> ConsoleUser:
    return ConsoleUser(method="test")


def test_decide_uses_authenticated_user_instead_of_forged_actor(monkeypatch) -> None:
    from src.console import manual_filter_admin_service

    captured: Dict[str, Any] = {}

    def bulk_decide(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {
            "selected": 1,
            "backup": 0,
            "discarded": 0,
            "pending": 0,
            "versions": {"a1": 2},
        }

    monkeypatch.setattr(manual_filter_admin_service, "bulk_decide", bulk_decide)
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        username="real-admin",
        display_name="真实管理员",
        role="admin",
    )
    client = TestClient(app)

    response = client.post(
        "/api/manual_filter/decide",
        json={
            "selected_ids": ["a1"],
            "actor": "forged-user",
            "report_type": "zongbao",
        },
    )

    assert response.status_code == 200
    assert captured["actor"].username == "real-admin"


def test_decide_api_returns_conflict_for_stale_manual_review(
    monkeypatch,
) -> None:
    from src.console import manual_filter_admin_service

    def bulk_decide(**kwargs: Any) -> Dict[str, Any]:
        del kwargs
        raise manual_filter_admin_service.ManualReviewConflictError(
            "Manual review version is stale for a1"
        )

    monkeypatch.setattr(manual_filter_admin_service, "bulk_decide", bulk_decide)
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id="real-user-id",
        username="real-admin",
        display_name="真实管理员",
        role="admin",
    )
    response = TestClient(app).post(
        "/api/manual_filter/decide",
        json={
            "selected_ids": ["a1"],
            "versions": {"a1": 7},
            "report_type": "zongbao",
        },
    )

    assert response.status_code == 409
    assert "stale" in response.json()["detail"]


def test_candidates_api_returns_search_mode_items(monkeypatch) -> None:
    from src.console import manual_filter_service

    adapter = FakeManualFilterAdapter(_build_rows())
    monkeypatch.setattr(manual_filter_service, "get_adapter", lambda: adapter)

    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    response = client.get(
        "/api/manual_filter/candidates",
        params={
            "region": "internal",
            "sentiment": "positive",
            "view_mode": "search",
            "q": "学科建设大会",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["view_mode"] == "search"
    assert payload["total"] == 1
    assert [item["article_id"] for item in payload["items"]] == ["a1"]


def test_discard_before_date_api_supports_keyword_only_preview_and_apply(monkeypatch) -> None:
    from src.console import manual_filter_admin_service, manual_filter_service

    adapter = FakeManualFilterAdapter(_build_rows())
    monkeypatch.setattr(manual_filter_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    preview = client.post(
        "/api/manual_filter/discard_before_date",
        json={
            "region": "internal",
            "sentiment": "positive",
            "q": "学科建设大会",
            "published_before": None,
            "actor": "tester",
            "dry_run": True,
        },
    )
    assert preview.status_code == 200
    assert preview.json() == {"matched": 1, "updated": 0}

    apply = client.post(
        "/api/manual_filter/discard_before_date",
        json={
            "region": "internal",
            "sentiment": "positive",
            "q": "学科建设大会",
            "published_before": None,
            "actor": "tester",
            "dry_run": False,
        },
    )
    assert apply.status_code == 200
    assert apply.json() == {"matched": 1, "updated": 1}
    assert next(row for row in adapter.rows if row["article_id"] == "a1")["status"] == "discarded"


def test_discard_before_date_api_supports_empty_optional_filters(monkeypatch) -> None:
    from src.console import manual_filter_admin_service, manual_filter_service

    adapter = FakeManualFilterAdapter(_build_rows())
    monkeypatch.setattr(manual_filter_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(manual_filter_admin_service, "get_adapter", lambda: adapter)

    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    response = client.post(
        "/api/manual_filter/discard_before_date",
        json={
            "region": "internal",
            "sentiment": "positive",
            "q": None,
            "published_before": None,
            "actor": "tester",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"matched": 2, "updated": 0}


def test_update_order_api_passes_review_groups(monkeypatch) -> None:
    from src.console import manual_filter_admin_service

    captured: Dict[str, Any] = {}

    def update_ranks(**kwargs: Any) -> Dict[str, int]:
        captured.update(kwargs)
        return {"selected": 1, "backup": 0, "updated_rows": 1, "updated_categories": 1}

    monkeypatch.setattr(manual_filter_admin_service, "update_ranks", update_ranks)
    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    response = client.post(
        "/api/manual_filter/order",
        json={
            "selected_order": ["a1"],
            "backup_order": [],
            "group_orders": {"external_positive": ["a1"]},
            "actor": "tester",
            "report_type": "zongbao",
        },
    )

    assert response.status_code == 200
    assert captured["group_orders"] == {"external_positive": ["a1"]}


def test_update_order_api_rejects_unknown_review_group() -> None:
    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    response = client.post(
        "/api/manual_filter/order",
        json={
            "selected_order": ["a1"],
            "group_orders": {"unknown": ["a1"]},
        },
    )

    assert response.status_code == 422


def test_duplicate_check_api_returns_review_groups(monkeypatch) -> None:
    from src.console import manual_filter_service

    expected = {
        "checked_count": 2,
        "model": "score-model",
        "groups": [{"group_id": "duplicate-1", "items": []}],
    }
    monkeypatch.setattr(manual_filter_service, "check_duplicates", lambda **kwargs: expected)

    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    response = client.post(
        "/api/manual_filter/duplicate-check",
        json={"report_type": "zongbao", "decision": "selected"},
    )

    assert response.status_code == 200
    assert response.json() == expected


def test_score_feedback_api_saves_and_clears(monkeypatch) -> None:
    from src.console import score_feedback_service

    captured: Dict[str, Any] = {}

    def save_score_feedback(**kwargs: Any) -> Dict[str, Any]:
        captured.update(kwargs)
        return {
            "feedback_type": kwargs["feedback_type"],
            "score_value": 82,
            "notes": kwargs["notes"],
            "updated_at": "2026-07-22T10:00:00Z",
        }

    monkeypatch.setattr(score_feedback_service, "save_score_feedback", save_score_feedback)
    monkeypatch.setattr(score_feedback_service, "clear_score_feedback", lambda **kwargs: True)
    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    saved = client.put(
        "/api/manual_filter/score-feedback",
        json={
            "article_id": "source/item/1",
            "feedback_type": "too_high",
            "notes": "分数偏高",
        },
    )
    cleared = client.post(
        "/api/manual_filter/score-feedback/clear",
        json={"article_id": "source/item/1"},
    )

    assert saved.status_code == 200
    assert saved.json()["score_feedback"]["feedback_type"] == "too_high"
    assert captured["article_id"] == "source/item/1"
    assert cleared.status_code == 200
    assert cleared.json() == {"score_feedback": None}


def test_score_feedback_api_rejects_invalid_payload() -> None:
    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    invalid_type = client.put(
        "/api/manual_filter/score-feedback",
        json={"article_id": "a1", "feedback_type": "correct"},
    )
    long_notes = client.put(
        "/api/manual_filter/score-feedback",
        json={"article_id": "a1", "feedback_type": "too_low", "notes": "a" * 501},
    )

    assert invalid_type.status_code == 422
    assert long_notes.status_code == 422


@pytest.mark.parametrize(
    ("error_type", "status_code"),
    [
        ("limit", 422),
        ("invalid", 502),
        ("unavailable", 503),
        ("timeout", 504),
    ],
)
def test_duplicate_check_api_maps_errors(
    error_type: str,
    status_code: int,
    monkeypatch,
) -> None:
    from src.console import manual_filter_service
    from src.console.manual_filter_duplicate_service import (
        DuplicateReviewInvalidResponseError,
        DuplicateReviewLimitError,
        DuplicateReviewTimeoutError,
        DuplicateReviewUnavailableError,
    )

    error_types = {
        "limit": DuplicateReviewLimitError,
        "invalid": DuplicateReviewInvalidResponseError,
        "unavailable": DuplicateReviewUnavailableError,
        "timeout": DuplicateReviewTimeoutError,
    }

    def raise_error(**kwargs):
        raise error_types[error_type]("duplicate check failed")

    monkeypatch.setattr(manual_filter_service, "check_duplicates", raise_error)
    app = create_app()
    app.dependency_overrides[require_console_user] = _anonymous_console_user
    client = TestClient(app)

    response = client.post(
        "/api/manual_filter/duplicate-check",
        json={"report_type": "wanbao", "decision": "backup"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": "duplicate check failed"}
