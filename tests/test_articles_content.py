from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi.testclient import TestClient

from src.console import articles_service
from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user


class FakeNewsSummariesNamespace:
    def __init__(
        self,
        row: Optional[dict[str, Any]],
        *,
        error: Optional[Exception] = None,
        latest_created_at: Optional[datetime] = None,
    ) -> None:
        self.row = row
        self.error = error
        self.latest_created_at = latest_created_at
        self.latest_bounds: Optional[dict[str, Optional[datetime]]] = None

    def fetch_content(self, article_id: str) -> Optional[dict[str, Any]]:
        if self.error is not None:
            raise self.error
        if self.row is None or self.row.get("article_id") != article_id:
            return None
        return dict(self.row)

    def fetch_latest_created_at(
        self,
        *,
        created_at_gte: Optional[datetime] = None,
        created_at_lt: Optional[datetime] = None,
    ) -> Optional[datetime]:
        if self.error is not None:
            raise self.error
        self.latest_bounds = {
            "created_at_gte": created_at_gte,
            "created_at_lt": created_at_lt,
        }
        return self.latest_created_at


class FakeAdapter:
    def __init__(
        self,
        row: Optional[dict[str, Any]],
        *,
        error: Optional[Exception] = None,
        latest_created_at: Optional[datetime] = None,
    ) -> None:
        self.news_summaries = FakeNewsSummariesNamespace(
            row,
            error=error,
            latest_created_at=latest_created_at,
        )


def _client(monkeypatch, adapter: FakeAdapter) -> TestClient:
    monkeypatch.setattr(articles_service, "_get_adapter_safe", lambda: adapter)
    app = create_app()
    app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
        method="test",
        user_id="editor-1",
        username="editor",
        display_name="值班编辑",
        role="duty_editor",
    )
    return TestClient(app)


def test_article_content_endpoints_return_identical_complete_contract(monkeypatch) -> None:
    long_content = "第一段原文。\n\n" + ("这是保留换行的正文内容。\n" * 220) + "\n最后一段。"
    row = {
        "article_id": "article-1",
        "title": "测试新闻标题",
        "source": "测试来源",
        "url": "https://example.com/article-1",
        "created_at": datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc),
        "content_markdown": long_content,
    }
    client = _client(monkeypatch, FakeAdapter(row))

    query_response = client.get(
        "/api/articles/content",
        params={"article_id": "article-1"},
    )
    legacy_response = client.get("/api/articles/article-1/content")

    assert query_response.status_code == 200
    assert legacy_response.status_code == 200
    assert query_response.json() == legacy_response.json() == {
        "article_id": "article-1",
        "title": "测试新闻标题",
        "source": "测试来源",
        "url": "https://example.com/article-1",
        "created_at": "2026-08-06T10:00:00Z",
        "content_markdown": long_content,
    }
    assert query_response.json()["content_markdown"] == long_content


def test_article_content_missing_record_returns_null_fields(monkeypatch) -> None:
    client = _client(monkeypatch, FakeAdapter(None))

    response = client.get(
        "/api/articles/content",
        params={"article_id": "missing-article"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "article_id": "missing-article",
        "title": None,
        "source": None,
        "url": None,
        "created_at": None,
        "content_markdown": None,
    }


def test_article_content_database_error_uses_null_fallback(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        FakeAdapter(None, error=RuntimeError("database unavailable")),
    )

    response = client.get("/api/articles/unavailable-article/content")

    assert response.status_code == 200
    assert response.json() == {
        "article_id": "unavailable-article",
        "title": None,
        "source": None,
        "url": None,
        "created_at": None,
        "content_markdown": None,
    }


def test_ingest_status_returns_latest_created_at(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        FakeAdapter(
            None,
            latest_created_at=datetime(2026, 8, 17, 2, 30, tzinfo=timezone.utc),
        ),
    )

    response = client.get("/api/articles/ingest-status")

    assert response.status_code == 200
    assert response.json() == {"latest_created_at": "2026-08-17T02:30:00Z"}


def test_ingest_status_service_forwards_half_open_bounds(monkeypatch) -> None:
    starts_at = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    ends_at = datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)
    adapter = FakeAdapter(
        None,
        latest_created_at=datetime(2026, 8, 21, 13, 50, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(articles_service, "_get_adapter_safe", lambda: adapter)

    result = articles_service.get_latest_ingest_status(
        created_at_gte=starts_at,
        created_at_lt=ends_at,
    )

    assert result["latest_created_at"] == adapter.news_summaries.latest_created_at
    assert adapter.news_summaries.latest_bounds == {
        "created_at_gte": starts_at,
        "created_at_lt": ends_at,
    }


def test_ingest_status_empty_database_returns_null(monkeypatch) -> None:
    client = _client(monkeypatch, FakeAdapter(None))

    response = client.get("/api/articles/ingest-status")

    assert response.status_code == 200
    assert response.json() == {"latest_created_at": None}


def test_ingest_status_database_error_uses_null_fallback(monkeypatch) -> None:
    client = _client(
        monkeypatch,
        FakeAdapter(None, error=RuntimeError("database unavailable")),
    )

    response = client.get("/api/articles/ingest-status")

    assert response.status_code == 200
    assert response.json() == {"latest_created_at": None}
