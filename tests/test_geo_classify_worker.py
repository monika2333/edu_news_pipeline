from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.adapters.llm_beijing_gate import (
    BeijingGateIndeterminateError,
    BeijingGateResponse,
)
from src.adapters.llm_chat import (
    LLM_SCOPE_ARTICLE,
    LLM_SCOPE_SYSTEMATIC,
    LLMQuotaError,
    LLMRequestError,
)
from src.domain.external_filter import BeijingGateCandidate
from src.domain.prompt import PromptTemplate
from src.workers import geo_classify


def _beijing_gate_candidate(**overrides) -> BeijingGateCandidate:
    base = dict(
        article_id="article-2",
        title="北京教育改革",
        source="示例来源",
        publish_time_iso=None,
        summary="摘要内容",
        content="正文内容",
        sentiment_label="positive",
        is_beijing_related=True,
        is_beijing_related_llm=None,
        external_importance_status="pending_beijing_gate",
        beijing_gate_fail_count=0,
        beijing_gate_attempted_at=None,
    )
    base.update(overrides)
    return BeijingGateCandidate(**base)


def _systematic_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="beijing_gate",
        model="deepseek-flash",
        status_code=400,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=False,
        endpoint_label="DeepSeek",
        provider_code="invalid_request_error",
        provider_message="This response_format type is unavailable now",
        request_id="req-systematic-1",
    )


def _article_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="beijing_gate",
        model="model-a",
        status_code=400,
        scope=LLM_SCOPE_ARTICLE,
        retryable=False,
        endpoint_label="OpenRouter",
        provider_code="invalid_request_error",
        provider_message="This model's maximum context length is 8192 tokens",
        request_id="req-article-1",
    )


class _DummyFuture:
    def __init__(self, result):
        self._result = result
        self.cancelled = False

    def result(self):
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result

    def cancel(self) -> bool:
        self.cancelled = True
        return True


class _DummyExecutor:
    def __init__(self, result_map):
        self._result_map = result_map
        self.calls = []
        self.futures = []

    def submit(self, func, candidate, retries, **kwargs):
        self.calls.append(
            {"func": func, "candidate": candidate, "retries": retries, **kwargs}
        )
        future = _DummyFuture(self._result_map[candidate.article_id])
        self.futures.append(future)
        return future


class _RecordingProcessNamespace:
    def __init__(self) -> None:
        self.candidates: list[BeijingGateCandidate] = []
        self.fetch_calls = 0
        self.completed: list[tuple[str, dict[str, object]]] = []
        self.failures: list[tuple[str, dict[str, object]]] = []

    def fetch_beijing_gate_candidates(
        self,
        limit: int,
        *,
        max_failures: int,
    ) -> list[BeijingGateCandidate]:
        del max_failures
        self.fetch_calls += 1
        return self.candidates[:limit]

    def complete_beijing_gate(self, article_id: str, **kwargs: object) -> None:
        self.completed.append((article_id, dict(kwargs)))

    def mark_beijing_gate_failure(self, article_id: str, **kwargs: object) -> None:
        self.failures.append((article_id, dict(kwargs)))


class _RecordingProcessAdapter:
    def __init__(self) -> None:
        self.process = _RecordingProcessNamespace()


def test_local_classification_stages_beijing_candidate() -> None:
    article = {
        "article_id": "article-1",
        "title": "北京教育新闻",
        "content_markdown": "正文",
        "llm_summary": "摘要",
        "sentiment_label": "positive",
    }

    assert geo_classify._determine_route(article, ["北京"]) == (
        True,
        "pending_beijing_gate",
    )


def test_process_beijing_gate_passes_internal_category() -> None:
    candidate = _beijing_gate_candidate()
    decision = SimpleNamespace(
        is_beijing_related=True,
        reason="明确属于北京市范围",
        raw_text="raw text",
    )
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: decision})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=1,
            max_failures=3,
        )

    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        1,
        0,
        0,
        0,
    )
    assert result.systematic_error is None
    kwargs = adapter.process.completed[0][1]
    assert kwargs["candidate_category"] == "internal_positive"
    assert kwargs["sentiment_label"] == "positive"
    assert kwargs["status"] == "ready_for_export"


def test_process_beijing_gate_reroutes_external_category() -> None:
    candidate = _beijing_gate_candidate(is_beijing_related=False)
    decision = SimpleNamespace(
        is_beijing_related=False,
        reason="判定为外省内容",
        raw_text="raw text",
    )
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: decision})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=1,
            max_failures=3,
        )

    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        0,
        1,
        0,
        0,
    )
    kwargs = adapter.process.completed[0][1]
    assert kwargs["candidate_category"] == "external_positive"
    assert kwargs["status"] == "pending_external_filter"
    assert kwargs["reset_external_filter"] is True


def test_process_beijing_gate_persists_indeterminate_response() -> None:
    candidate = _beijing_gate_candidate()
    error = BeijingGateIndeterminateError(
        BeijingGateResponse(
            raw_text='{"is_behind_related": true, "reason": "字段错误"}',
            provider="provider-a",
            model="model-a",
        ),
        attempts=3,
    )
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: error})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=3,
            max_failures=3,
        )

    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        0,
        0,
        1,
        0,
    )
    kwargs = adapter.process.failures[0][1]
    assert kwargs["fail_count"] == 1
    assert kwargs["raw_output"] == {
        "error": "Beijing gate returned indeterminate result",
        "fail_count": 1,
        "model_output": error.raw_text,
        "provider": "provider-a",
        "model": "model-a",
        "semantic_attempts": 3,
    }


def test_gate_backlog_does_not_retry_failed_candidate_in_same_run() -> None:
    candidate = _beijing_gate_candidate()
    error = BeijingGateIndeterminateError(
        BeijingGateResponse(
            raw_text='{"is_behind_related": true}',
            provider="provider-a",
            model="model-a",
        ),
        attempts=3,
    )
    adapter = _RecordingProcessAdapter()
    adapter.process.candidates = [candidate]
    executor = _DummyExecutor({candidate.article_id: error})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_gate_backlog(
            adapter,
            executor,
            limit=10,
            batch_size=5,
            llm_retries=3,
            max_failures=3,
        )

    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        0,
        0,
        1,
        0,
    )
    assert result.systematic_error is None
    assert adapter.process.fetch_calls == 2
    assert len(adapter.process.failures) == 1


def test_process_beijing_gate_systematic_error_aborts_without_writes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = _beijing_gate_candidate(article_id="article-a")
    second = _beijing_gate_candidate(article_id="article-b")
    error = _systematic_request_error()
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor(
        {
            "article-a": error,
            "article-b": SimpleNamespace(
                is_beijing_related=True,
                reason="不应被处理",
                raw_text="raw",
            ),
        }
    )
    alerts: list[dict[str, object]] = []

    def fake_alert(**kwargs: object) -> bool:
        alerts.append(kwargs)
        return True

    monkeypatch.setattr(geo_classify, "notify_llm_request_alert", fake_alert)

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [first, second],
            executor,
            llm_retries=3,
            max_failures=3,
        )

    assert result.systematic_error is error
    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        0,
        0,
        0,
        0,
    )
    assert adapter.process.completed == []
    assert adapter.process.failures == []
    assert len(alerts) == 1
    assert alerts[0]["request_id"] == "req-systematic-1"
    assert alerts[0]["status_code"] == 400
    assert alerts[0]["endpoint_label"] == "DeepSeek"
    assert executor.futures[1].cancelled is True
    assert capsys.readouterr().out.count("ERROR run:") == 1


def test_process_beijing_gate_quota_error_aborts_without_request_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _beijing_gate_candidate()
    error = LLMQuotaError(
        operation="beijing_gate",
        model="model-a",
        status_code=402,
        response_text="Insufficient Balance",
    )
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: error})
    alerts: list[dict[str, object]] = []
    monkeypatch.setattr(
        geo_classify,
        "notify_llm_request_alert",
        lambda **kwargs: alerts.append(kwargs) or True,
    )

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=3,
            max_failures=3,
        )

    assert result.systematic_error is error
    assert result.failures == 0
    assert adapter.process.completed == []
    assert adapter.process.failures == []
    # 额度错误有独立的 LLMQuotaError 告警，这里不再重复发请求错误告警。
    assert alerts == []


def test_process_beijing_gate_article_error_counts_one_failure() -> None:
    candidate = _beijing_gate_candidate()
    error = _article_request_error()
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: error})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=3,
            max_failures=3,
        )

    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        0,
        0,
        1,
        0,
    )
    assert len(executor.calls) == 1
    assert adapter.process.completed == []
    article_id, kwargs = adapter.process.failures[0]
    assert article_id == candidate.article_id
    assert kwargs["fail_count"] == 1
    assert "final_status" not in kwargs
    raw_output = kwargs["raw_output"]
    assert isinstance(raw_output, dict)
    assert raw_output["scope"] == LLM_SCOPE_ARTICLE
    assert raw_output["status_code"] == 400
    assert raw_output["request_id"] == "req-article-1"


def test_process_beijing_gate_article_error_exhausts_without_beijing_fallback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate = _beijing_gate_candidate(beijing_gate_fail_count=2)
    error = _article_request_error()
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: error})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=3,
            max_failures=3,
        )

    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        0,
        0,
        1,
        0,
    )
    # 不能再出现 ready_for_export 兜底：文章保持 pending_beijing_gate 成为死信。
    assert adapter.process.completed == []
    kwargs = adapter.process.failures[0][1]
    assert kwargs["fail_count"] == 3
    assert "final_status" not in kwargs
    assert "GATE EXHAUSTED" in capsys.readouterr().out


def test_process_beijing_gate_retryable_error_keeps_existing_fallback() -> None:
    candidate = _beijing_gate_candidate(beijing_gate_fail_count=2)
    error = LLMRequestError(
        operation="beijing_gate",
        model="model-a",
        status_code=500,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=True,
        endpoint_label="OpenRouter",
        provider_message="upstream internal error",
    )
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: error})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        result = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=3,
            max_failures=3,
        )

    assert (result.confirmed, result.rerouted, result.failures, result.fallbacks) == (
        0,
        0,
        1,
        1,
    )
    kwargs = adapter.process.completed[0][1]
    assert kwargs["status"] == "ready_for_export"
    assert kwargs["raw_output"]["fallback"] == "ready_for_export"


class _NewsSummariesNamespace:
    def __init__(self, adapter: _RunAdapter) -> None:
        self._adapter = adapter

    def fetch_pending_routes(self, limit: int) -> list[dict[str, object]]:
        return [
            {
                "article_id": self._adapter.candidate.article_id,
                "title": self._adapter.candidate.title,
                "content_markdown": self._adapter.candidate.content,
                "llm_summary": self._adapter.candidate.summary,
                "sentiment_label": self._adapter.candidate.sentiment_label,
            }
        ][:limit]

    def complete_routing(
        self,
        article_id: str,
        *,
        beijing_related: bool | None,
        status: str,
    ) -> None:
        self._adapter.local_updates.append((article_id, beijing_related, status))


class _RunProcessNamespace:
    def __init__(self, adapter: _RunAdapter) -> None:
        self._adapter = adapter

    def fetch_beijing_gate_candidates(
        self,
        limit: int,
        *,
        max_failures: int,
    ) -> list[BeijingGateCandidate]:
        del max_failures
        self._adapter.gate_fetch_calls += 1
        return [self._adapter.candidate][:limit]

    def complete_beijing_gate(self, article_id: str, **kwargs: object) -> None:
        del kwargs
        self._adapter.gate_updates.append(article_id)

    def mark_beijing_gate_failure(self, article_id: str, **kwargs: object) -> None:
        del kwargs
        raise AssertionError(f"unexpected Beijing gate failure: {article_id}")


class _RunAdapter:
    def __init__(self, candidate: BeijingGateCandidate) -> None:
        self.candidate = candidate
        self.local_updates: list[tuple[str, bool | None, str]] = []
        self.gate_updates: list[str] = []
        self.gate_fetch_calls = 0
        self.news_summaries = _NewsSummariesNamespace(self)
        self.process = _RunProcessNamespace(self)


def test_run_combines_local_routing_and_beijing_gate(monkeypatch) -> None:
    from src.business_config import business_config_context
    from tests.conftest import make_endpoint_config

    candidate = _beijing_gate_candidate()
    adapter = _RunAdapter(candidate)
    settings = SimpleNamespace(
        process_limit=None,
        default_concurrency=2,
        external_filter_batch_size=10,
        beijing_gate_max_retries=1,
    )
    decision = SimpleNamespace(
        is_beijing_related=True,
        reason="明确属于北京市范围",
        raw_text="raw text",
    )
    monkeypatch.setattr(geo_classify, "get_adapter", lambda: adapter)
    monkeypatch.setattr(geo_classify, "get_settings", lambda: settings)
    monkeypatch.setattr(
        geo_classify,
        "load_active_prompts",
        lambda adapter: {
            "beijing_gate": PromptTemplate(
                key="beijing_gate",
                version="v9",
                content="GATE TEMPLATE",
                source="database",
                revision=9,
                revision_id=90,
            )
        },
    )
    monkeypatch.setattr(
        geo_classify,
        "call_beijing_gate",
        lambda candidate, retries, prompt_template=None: decision,
    )

    config = make_endpoint_config(
        beijing_keywords=("北京",),
        education_keywords=("教育",),
    )
    with business_config_context(config):
        geo_classify.run(limit=1, concurrency=2)

    assert adapter.local_updates == [
        (candidate.article_id, True, "pending_beijing_gate")
    ]
    assert adapter.gate_updates == [candidate.article_id]


def _run_geo_classify_with_gate_error(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> tuple[_RunAdapter, list[dict[str, object]]]:
    from src.business_config import business_config_context
    from tests.conftest import make_endpoint_config

    candidate = _beijing_gate_candidate()
    adapter = _RunAdapter(candidate)
    settings = SimpleNamespace(
        process_limit=None,
        default_concurrency=2,
        external_filter_batch_size=10,
        beijing_gate_max_retries=3,
    )
    alerts: list[dict[str, object]] = []

    def fail_gate(*args: object, **kwargs: object) -> object:
        raise error

    monkeypatch.setattr(geo_classify, "get_adapter", lambda: adapter)
    monkeypatch.setattr(geo_classify, "get_settings", lambda: settings)
    monkeypatch.setattr(
        geo_classify,
        "load_active_prompts",
        lambda adapter: {
            "beijing_gate": PromptTemplate(
                key="beijing_gate",
                version="v9",
                content="GATE TEMPLATE",
                source="database",
                revision=9,
                revision_id=90,
            )
        },
    )
    monkeypatch.setattr(geo_classify, "call_beijing_gate", fail_gate)
    monkeypatch.setattr(
        geo_classify,
        "notify_llm_request_alert",
        lambda **kwargs: alerts.append(kwargs) or True,
    )

    config = make_endpoint_config(
        beijing_keywords=("北京",),
        education_keywords=("教育",),
    )
    with business_config_context(config):
        with pytest.raises(type(error)) as exc_info:
            geo_classify.run(limit=1, concurrency=1)

    assert exc_info.value is error
    return adapter, alerts


def test_run_fails_and_stops_after_systematic_gate_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = _systematic_request_error()

    adapter, alerts = _run_geo_classify_with_gate_error(monkeypatch, error)

    assert adapter.gate_updates == []
    assert adapter.gate_fetch_calls == 1
    assert len(alerts) == 1
    assert alerts[0]["request_id"] == "req-systematic-1"


def test_run_fails_after_quota_gate_error(monkeypatch: pytest.MonkeyPatch) -> None:
    error = LLMQuotaError(
        operation="beijing_gate",
        model="model-a",
        status_code=402,
        response_text="Insufficient Balance",
    )

    adapter, alerts = _run_geo_classify_with_gate_error(monkeypatch, error)

    assert adapter.gate_updates == []
    assert adapter.gate_fetch_calls == 1
    assert alerts == []


def test_process_beijing_gate_passes_prompt_template_to_model():
    candidate = _beijing_gate_candidate()
    decision = SimpleNamespace(
        is_beijing_related=True,
        reason="明确属于北京市范围",
        raw_text="raw text",
    )
    adapter = _RecordingProcessAdapter()
    executor = _DummyExecutor({candidate.article_id: decision})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=1,
            max_failures=3,
            prompt_template="GATE TEMPLATE",
        )

    assert executor.calls[0]["prompt_template"] == "GATE TEMPLATE"
    assert executor.calls[0]["retries"] == 1
