from __future__ import annotations

from threading import Barrier
from typing import Any, Optional

from src.workers import enrich_summary


class _NewsSummariesNamespace:
    def __init__(self, adapter: _EnrichmentAdapter) -> None:
        self._adapter = adapter

    def fetch_pending_enrichments(self, limit: int) -> list[dict[str, Any]]:
        return self._adapter.rows[:limit]

    def complete_enrichment(
        self,
        article_id: str,
        *,
        label: str,
        confidence: Optional[float],
        llm_source: Optional[str],
    ) -> None:
        self._adapter.completed.append((article_id, label, confidence, llm_source))


class _EnrichmentAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.completed: list[tuple[str, str, Optional[float], Optional[str]]] = []
        self.news_summaries = _NewsSummariesNamespace(self)


def _row() -> dict[str, Any]:
    return {
        "article_id": "article-1",
        "title": "测试标题",
        "content_markdown": "正文内容",
        "llm_summary": "摘要内容",
    }


def test_sentiment_and_source_are_independent_concurrent_requests(monkeypatch) -> None:
    adapter = _EnrichmentAdapter([_row()])
    barrier = Barrier(2, timeout=2)
    monkeypatch.setattr(enrich_summary, "get_adapter", lambda: adapter)

    def classify(summary_text: str) -> dict[str, Any]:
        barrier.wait()
        return {"label": "positive", "confidence": 0.9}

    def detect(article: dict[str, Any]) -> dict[str, Any]:
        barrier.wait()
        return {"llm_source": "测试媒体"}

    monkeypatch.setattr(enrich_summary, "classify_sentiment", classify)
    monkeypatch.setattr(enrich_summary, "detect_source", detect)

    enrich_summary.run(limit=1, concurrency=2)

    assert adapter.completed == [("article-1", "positive", 0.9, "测试媒体")]


def test_failed_request_keeps_entire_enrichment_pending(monkeypatch) -> None:
    adapter = _EnrichmentAdapter([_row()])
    monkeypatch.setattr(enrich_summary, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        enrich_summary,
        "classify_sentiment",
        lambda summary_text: {"label": "negative", "confidence": 0.8},
    )
    monkeypatch.setattr(
        enrich_summary,
        "detect_source",
        lambda article: (_ for _ in ()).throw(RuntimeError("source failed")),
    )

    enrich_summary.run(limit=1, concurrency=2)

    assert adapter.completed == []


def test_source_length_guard_logs_article_id_and_metadata(monkeypatch) -> None:
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        enrich_summary,
        "detect_source",
        lambda article: {
            "llm_source": None,
            "source_guard_discarded_length": 945,
            "source_guard_triggered_attempt": 1,
        },
    )
    monkeypatch.setattr(
        enrich_summary,
        "log_info",
        lambda worker, message: messages.append((worker, message)),
    )

    result = enrich_summary._detect_article_source(_row())

    assert result.llm_source is None
    assert messages == [
        (
            "enrich_summary",
            "WARNING source length guard article_id=article-1 "
            "discarded_length=945 attempt=1",
        )
    ]
