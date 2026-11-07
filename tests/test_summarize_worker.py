from __future__ import annotations

from threading import Barrier
from typing import Any

import pytest

from src.adapters.llm_chat import (
    LLM_SCOPE_SYSTEMATIC,
    LLMEndpointBlockedError,
    LLMQuotaError,
    LLMRequestError,
)
from src.business_config import BusinessConfigError
from src.workers import llm_failures, summarize


class _NewsSummariesNamespace:
    def __init__(self, adapter: _RunAdapter) -> None:
        self._adapter = adapter

    def fetch_pending(self, limit: int, *, max_attempts: int) -> list[dict[str, Any]]:
        del max_attempts
        return self._adapter.rows[:limit]

    def mark_attempt(self, article_id: str) -> bool:
        self._adapter.attempted.append(article_id)
        self._adapter.counts[article_id] = (
            self._adapter.counts.get(article_id, 0) + 1
        )
        return True

    def refund_attempt(self, article_id: str) -> None:
        self._adapter.refunded.append(article_id)
        if self._adapter.counts.get(article_id, 0) > 0:
            self._adapter.counts[article_id] -= 1

    def complete_generation(self, article_id: str, summary_text: str) -> None:
        self._adapter.completed.append((article_id, summary_text))

    def mark_failed(self, article_id: str, *, message: str | None = None) -> None:
        del message
        self._adapter.failed.append(article_id)


class _RunAdapter:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.attempted: list[str] = []
        self.refunded: list[str] = []
        self.completed: list[tuple[str, str]] = []
        self.failed: list[str] = []
        self.counts: dict[str, int] = {
            row["article_id"]: int(row.get("summary_fail_count") or 0)
            for row in rows
        }
        self.news_summaries = _NewsSummariesNamespace(self)


def _row(article_id: str = "article-1", *, fail_count: int = 0) -> dict[str, Any]:
    return {
        "article_id": article_id,
        "title": "测试标题",
        "content_markdown": "正文内容",
        "summary_fail_count": fail_count,
    }


def _systematic_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="summarize",
        model="deepseek-flash",
        status_code=400,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=False,
        endpoint_label="DeepSeek",
        provider_code="invalid_request_error",
        provider_message="This response_format type is unavailable now",
        request_id="req-systematic-1",
    )


def _record_alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []

    def fake_alert(**kwargs: object) -> bool:
        alerts.append(kwargs)
        return True

    monkeypatch.setattr(llm_failures, "notify_llm_request_alert", fake_alert)
    return alerts


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
    adapter = _RunAdapter([_row(fail_count=2)])
    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        summarize,
        "summarise",
        lambda article: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    summarize.run(limit=1, concurrency=1)

    assert adapter.completed == []
    assert adapter.failed == ["article-1"]
    assert adapter.refunded == []


def test_systematic_error_refunds_attempt_and_never_marks_failed(
    monkeypatch,
) -> None:
    # summary_fail_count=2 means this request was the third attempt: even at the
    # retry ceiling a systematic error must not mark the article failed.
    adapter = _RunAdapter([_row(fail_count=2)])
    error = _systematic_request_error()
    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        summarize,
        "summarise",
        lambda article: (_ for _ in ()).throw(error),
    )
    _record_alerts(monkeypatch)

    summarize.run(limit=1, concurrency=1)

    assert adapter.attempted == ["article-1"]
    assert adapter.refunded == ["article-1"]
    assert adapter.counts == {"article-1": 2}
    assert adapter.failed == []
    assert adapter.completed == []


def test_transient_error_keeps_count_and_fails_at_retry_ceiling(monkeypatch) -> None:
    adapter = _RunAdapter([_row(fail_count=2)])
    error = LLMRequestError(
        operation="summarize",
        model="model-a",
        status_code=503,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=True,
        provider_message="upstream unavailable",
    )
    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        summarize,
        "summarise",
        lambda article: (_ for _ in ()).throw(error),
    )

    summarize.run(limit=1, concurrency=1)

    assert adapter.refunded == []
    assert adapter.counts == {"article-1": 3}
    assert adapter.failed == ["article-1"]


@pytest.mark.parametrize(
    "error",
    [
        _systematic_request_error(),
        LLMEndpointBlockedError("接入点主机不在 LLM_ALLOWED_HOSTS 白名单内"),
        LLMQuotaError(
            operation="summarize",
            model="model-a",
            status_code=402,
            response_text="Insufficient Balance",
        ),
        BusinessConfigError("缺少模型配置步骤"),
    ],
    ids=["systematic", "blocked_host", "quota", "business_config"],
)
def test_run_alerts_at_most_once_for_three_systematic_failures(
    monkeypatch,
    error: Exception,
) -> None:
    adapter = _RunAdapter(
        [_row(f"article-{index}") for index in range(3)]
    )
    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        summarize,
        "summarise",
        lambda article: (_ for _ in ()).throw(error),
    )
    alerts = _record_alerts(monkeypatch)

    summarize.run(limit=3, concurrency=2)

    assert sorted(adapter.refunded) == ["article-0", "article-1", "article-2"]
    assert adapter.failed == []
    if isinstance(error, LLMQuotaError):
        assert alerts == []
    else:
        assert len(alerts) == 1
        assert alerts[0]["operation"] == "summarize"


def test_run_swallows_request_alert_failure(monkeypatch) -> None:
    adapter = _RunAdapter([_row()])
    error = _systematic_request_error()
    monkeypatch.setattr(summarize, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        summarize,
        "summarise",
        lambda article: (_ for _ in ()).throw(error),
    )

    def broken_alert(**kwargs: object) -> bool:
        raise RuntimeError("feishu down")

    monkeypatch.setattr(llm_failures, "notify_llm_request_alert", broken_alert)

    summarize.run(limit=1, concurrency=1)

    assert adapter.refunded == ["article-1"]
    assert adapter.failed == []
