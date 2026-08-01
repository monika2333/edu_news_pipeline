from __future__ import annotations

from threading import Barrier
from typing import Any

from src.workers import summarize


class _NewsSummariesNamespace:
    def __init__(self, adapter: _RunAdapter) -> None:
        self._adapter = adapter

    def fetch_pending(self, limit: int, *, max_attempts: int) -> list[dict[str, Any]]:
        del max_attempts
        return self._adapter.rows[:limit]

    def mark_attempt(self, article_id: str) -> bool:
        self._adapter.attempted.append(article_id)
        return True

    def complete_generation(self, article_id: str, summary_text: str) -> None:
        self._adapter.completed.append((article_id, summary_text))

    def mark_failed(self, article_id: str, *, message: str | None = None) -> None:
        del message
        self._adapter.failed.append(article_id)


class _RunAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.attempted: list[str] = []
        self.completed: list[tuple[str, str]] = []
        self.failed: list[str] = []
        self.news_summaries = _NewsSummariesNamespace(self)


def test_run_generates_only_summaries_concurrently(monkeypatch) -> None:
    rows = [
        {
            "article_id": f"article-{index}",
            "title": f"测试标题 {index}",
            "content_markdown": f"正文内容 {index}",
            "summary_fail_count": 0,
        }
        for index in range(2)
    ]
    adapter = _RunAdapter(rows)
    barrier = Barrier(2, timeout=2)

    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)

    def summarize_concurrently(article: dict[str, Any]) -> dict[str, str]:
        barrier.wait()
        return {"summary": f"摘要：{article['title']}"}

    monkeypatch.setattr(summarize, "summarise", summarize_concurrently)

    summarize.run(limit=2, concurrency=2)

    assert adapter.attempted == ["article-0", "article-1"]
    assert {item[0] for item in adapter.completed} == {"article-0", "article-1"}
    assert adapter.failed == []


def test_summary_failure_is_terminal_on_third_worker_attempt(monkeypatch) -> None:
    adapter = _RunAdapter(
        [
            {
                "article_id": "article-1",
                "title": "测试标题",
                "content_markdown": "正文内容",
                "summary_fail_count": 2,
            }
        ]
    )
    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        summarize,
        "summarise",
        lambda article: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    summarize.run(limit=1, concurrency=1)

    assert adapter.completed == []
    assert adapter.failed == ["article-1"]
