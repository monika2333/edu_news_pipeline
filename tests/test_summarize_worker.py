from __future__ import annotations

from threading import Barrier
from typing import Any

from src.workers import summarize


class _DummyFuture:
    def __init__(self, result: summarize.SummaryResult) -> None:
        self._result = result

    def result(self) -> summarize.SummaryResult:
        return self._result


class _FakeAdapter:
    def __init__(self) -> None:
        self.completed: list[dict[str, Any]] = []

    def complete_summary(self, article_id: str, summary_text: str, **kwargs: Any) -> None:
        self.completed.append(
            {
                "article_id": article_id,
                "summary_text": summary_text,
                **kwargs,
            }
        )

    def mark_summary_failed(self, article_id: str, *, message: str | None = None) -> None:
        raise AssertionError(f"unexpected summary failure for {article_id}: {message}")


def test_process_result_does_not_overwrite_llm_source_when_detection_unknown() -> None:
    adapter = _FakeAdapter()
    article = {
        "article_id": "article-1",
        "title": "测试标题",
        "content_markdown": "正文内容",
        "llm_keywords": [],
    }
    stats = summarize.SummaryStats()

    summarize._process_result(
        (
            _DummyFuture(
                summarize.SummaryResult(
                    summary_text="摘要内容",
                    sentiment_label="positive",
                    sentiment_confidence=0.8,
                    llm_source=None,
                    source_error=None,
                    summary_seconds=1.0,
                    sentiment_seconds=1.0,
                    source_seconds=1.0,
                )
            ),
            article,
            "article-1",
            1,
        ),
        adapter,
        [],
        stats,
    )

    assert stats.success == 1
    assert adapter.completed
    assert adapter.completed[0]["llm_source"] is None


class _RunAdapter(_FakeAdapter):
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self.rows = rows
        self.attempted: list[str] = []

    def fetch_pending_summaries(self, limit: int, *, max_attempts: int) -> list[dict[str, Any]]:
        return self.rows[:limit]

    def mark_summary_attempt(self, article_id: str) -> bool:
        self.attempted.append(article_id)
        return True


def test_run_processes_post_summary_llm_calls_concurrently(monkeypatch) -> None:
    rows = [
        {
            "article_id": f"article-{index}",
            "title": f"测试标题 {index}",
            "content_markdown": f"正文内容 {index}",
            "llm_keywords": [],
            "summary_fail_count": 0,
        }
        for index in range(2)
    ]
    adapter = _RunAdapter(rows)
    sentiment_barrier = Barrier(2, timeout=2)

    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(summarize, "load_beijing_keywords", lambda path: [])
    monkeypatch.setattr(
        summarize,
        "summarise",
        lambda article: {"summary": f"摘要：{article['title']}"},
    )

    def classify_concurrently(summary_text: str) -> dict[str, Any]:
        sentiment_barrier.wait()
        return {"label": "positive", "confidence": 0.9}

    monkeypatch.setattr(summarize, "classify_sentiment", classify_concurrently)
    monkeypatch.setattr(summarize, "detect_source", lambda article: {"llm_source": "测试媒体"})

    summarize.run(limit=2, concurrency=2)

    assert adapter.attempted == ["article-0", "article-1"]
    assert {item["article_id"] for item in adapter.completed} == {"article-0", "article-1"}
