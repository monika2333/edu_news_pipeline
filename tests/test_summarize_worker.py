from __future__ import annotations

from typing import Any

from src.workers import summarize


class _DummyFuture:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def result(self) -> dict[str, Any]:
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


def test_process_result_does_not_overwrite_llm_source_when_detection_unknown(monkeypatch) -> None:
    adapter = _FakeAdapter()
    article = {
        "article_id": "article-1",
        "title": "测试标题",
        "content_markdown": "正文内容",
        "llm_keywords": [],
    }
    stats = summarize.SummaryStats()

    monkeypatch.setattr(
        summarize,
        "classify_sentiment",
        lambda summary_text: {"label": "positive", "confidence": 0.8},
    )
    monkeypatch.setattr(summarize, "detect_source", lambda payload: {"llm_source": None})

    summarize._process_result(
        (_DummyFuture({"summary": "摘要内容"}), article, "article-1", 1),
        adapter,
        [],
        stats,
    )

    assert stats.success == 1
    assert adapter.completed
    assert adapter.completed[0]["llm_source"] is None
