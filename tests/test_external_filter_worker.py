from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.adapters import external_filter_model
from src.adapters.llm_chat import (
    LLM_SCOPE_SYSTEMATIC,
    LLMEndpointBlockedError,
    LLMQuotaError,
    LLMRequestError,
)
from src.business_config import BusinessConfigError
from src.domain.external_filter import ExternalFilterCandidate
from src.domain.prompt import PromptTemplate
from src.workers import external_filter, llm_failures


def _external_candidate(**overrides) -> ExternalFilterCandidate:
    base = dict(
        article_id="article-1",
        title="示例标题",
        source="示例来源",
        publish_time_iso=None,
        summary="摘要内容",
        content="正文内容",
        sentiment_label="positive",
        is_beijing_related=True,
        is_beijing_related_llm=None,
        external_importance_status="pending_external_filter",
        external_filter_fail_count=0,
        keyword_matches=(),
    )
    base.update(overrides)
    return ExternalFilterCandidate(**base)


def _prompt(key: str, version: str = "v7") -> PromptTemplate:
    return PromptTemplate(
        key=key,
        version=version,
        content=f"{key} TEMPLATE",
        source="database",
        revision=7,
        revision_id=70,
    )


def _systematic_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="external_filter:internal_positive",
        model="deepseek-flash",
        status_code=400,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=False,
        endpoint_label="DeepSeek",
        provider_code="invalid_request_error",
        provider_message="This response_format type is unavailable now",
        request_id="req-systematic-1",
    )


def _retryable_request_error() -> LLMRequestError:
    return LLMRequestError(
        operation="external_filter:internal_positive",
        model="model-a",
        status_code=500,
        scope=LLM_SCOPE_SYSTEMATIC,
        retryable=True,
        provider_message="upstream internal error",
    )


def _record_alerts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    alerts: list[dict[str, object]] = []

    def fake_alert(**kwargs: object) -> bool:
        alerts.append(kwargs)
        return True

    monkeypatch.setattr(llm_failures, "notify_llm_request_alert", fake_alert)
    return alerts


class _DummyFuture:
    def __init__(self, result: object) -> None:
        self._result = result

    def result(self):
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _DummyExecutor:
    def __init__(self, result_map: dict[str, object]) -> None:
        self._result_map = result_map
        self.calls: list[dict[str, object]] = []

    def submit(self, func, candidate, **kwargs):
        self.calls.append(
            {"func": func, "candidate": candidate, **kwargs}
        )
        return _DummyFuture(self._result_map[candidate.article_id])


class _BatchAdapter:
    def __init__(self) -> None:
        self.completed: list[tuple[str, dict[str, object]]] = []
        self.failures: list[tuple[str, dict[str, object]]] = []
        self.process = SimpleNamespace(
            mark_external_filter_failure=self._mark_failure
        )

    def _mark_failure(self, article_id: str, **kwargs: object) -> None:
        self.failures.append((article_id, dict(kwargs)))

    def complete_external_filter(self, article_id: str, **kwargs: object) -> None:
        self.completed.append((article_id, dict(kwargs)))


def test_score_candidate_uses_internal_threshold_and_category():
    candidate = _external_candidate()
    thresholds = {"external": 30, "internal": 60, "internal_positive": 60}
    with patch(
        "src.workers.external_filter.call_external_filter_model", return_value="88"
    ) as mock_call:
        score, raw, passed, category, prompt_key, prompt_version = external_filter._score_candidate(
            candidate,
            retries=2,
            thresholds=thresholds,
            prompt=_prompt("internal_positive"),
        )
    mock_call.assert_called_once_with(
        candidate,
        category="internal_positive",
        retries=2,
        prompt_template="internal_positive TEMPLATE",
    )
    assert score == 88
    assert raw == "88"
    assert passed is True
    assert category == "internal_positive"
    assert prompt_key == "internal_positive"
    assert prompt_version == "v7"


def test_score_candidate_respects_internal_threshold():
    candidate = _external_candidate()
    thresholds = {"external": 30, "internal": 60, "internal_positive": 60}
    with patch(
        "src.workers.external_filter.call_external_filter_model", return_value="40"
    ):
        score, raw, passed, category, prompt_key, prompt_version = external_filter._score_candidate(
            candidate,
            retries=1,
            thresholds=thresholds,
            prompt=_prompt("internal_positive"),
        )
    assert score == 40
    assert passed is False
    assert category == "internal_positive"
    assert prompt_key == "internal_positive"
    assert prompt_version == "v7"


def test_score_candidate_uses_external_negative_threshold():
    candidate = _external_candidate(sentiment_label="negative", is_beijing_related=False)
    thresholds = {
        "external": 60,
        "external_positive": 60,
        "external_negative": 30,
    }
    with patch(
        "src.workers.external_filter.call_external_filter_model",
        return_value="40",
    ) as mock_call:
        score, raw, passed, category, prompt_key, prompt_version = external_filter._score_candidate(
            candidate,
            retries=1,
            thresholds=thresholds,
            prompt=_prompt("external_negative", version="v4"),
        )
    mock_call.assert_called_once_with(
        candidate,
        category="external_negative",
        retries=1,
        prompt_template="external_negative TEMPLATE",
    )
    assert score == 40
    assert raw == "40"
    assert passed is True  # uses the lower negative threshold
    assert category == "external_negative"
    assert prompt_key == "external_negative"
    assert prompt_version == "v4"


def test_internal_prompt_includes_bonus_keywords():
    candidate = _external_candidate(keyword_matches=("北京教育改革", "首都治理"))
    with patch(
        "src.adapters.external_filter_model._load_prompt_template", return_value="PROMPT"
    ):
        prompt = external_filter_model.build_prompt(candidate, category="internal")
    assert "Bonus Keywords: 北京教育改革、首都治理" in prompt
    assert "PROMPT" in prompt


def test_batch_systematic_error_does_not_touch_article_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _external_candidate(article_id="article-a"),
        _external_candidate(article_id="article-b", external_filter_fail_count=1),
    ]
    adapter = _BatchAdapter()
    executor = _DummyExecutor(
        {
            "article-a": _systematic_request_error(),
            "article-b": BusinessConfigError("缺少模型配置步骤"),
        }
    )
    alerts = _record_alerts(monkeypatch)

    with patch(
        "src.workers.external_filter.as_completed", lambda futures: list(futures)
    ):
        processed, failed, filter_ready = external_filter._process_external_filter_batch(
            adapter,
            candidates,
            executor,
            {"internal_positive": 60},
            max_retries=3,
            remaining_limit=None,
            prompts={"internal_positive": _prompt("internal_positive")},
        )
    assert (processed, failed, filter_ready) == (0, 2, 0)
    assert adapter.failures == []
    assert adapter.completed == []
    assert len(alerts) == 1
    assert alerts[0]["operation"] == "external_filter"


def test_batch_transient_and_plain_errors_still_count_failures() -> None:
    candidates = [
        _external_candidate(article_id="article-a", external_filter_fail_count=1),
        _external_candidate(article_id="article-b", external_filter_fail_count=2),
    ]
    adapter = _BatchAdapter()
    executor = _DummyExecutor(
        {
            "article-a": _retryable_request_error(),
            "article-b": RuntimeError("Model did not return a numeric score"),
        }
    )

    with patch(
        "src.workers.external_filter.as_completed", lambda futures: list(futures)
    ):
        processed, failed, filter_ready = external_filter._process_external_filter_batch(
            adapter,
            candidates,
            executor,
            {"internal_positive": 60},
            max_retries=3,
            remaining_limit=None,
            prompts={"internal_positive": _prompt("internal_positive")},
        )

    assert (processed, failed, filter_ready) == (0, 2, 0)
    assert [
        (article_id, kwargs["fail_count"], kwargs["final_failure"])
        for article_id, kwargs in adapter.failures
    ] == [
        ("article-a", 2, False),
        ("article-b", 3, True),
    ]


class _RunProcessNamespace:
    def __init__(self, adapter: _RunAdapter) -> None:
        self._adapter = adapter

    def fetch_external_filter_candidates(
        self,
        limit: int,
        *,
        max_failures: int,
    ) -> list[ExternalFilterCandidate]:
        del max_failures
        self._adapter.fetch_calls += 1
        if self._adapter.fetch_calls > 8:
            raise AssertionError("run() did not terminate: fetch loop kept returning candidates")
        return self._adapter.candidates[:limit]

    def mark_external_filter_failure(self, article_id: str, **kwargs: object) -> None:
        self._adapter.failures.append((article_id, dict(kwargs)))


class _RunAdapter:
    def __init__(self, candidates: list[ExternalFilterCandidate]) -> None:
        self.candidates = candidates
        self.fetch_calls = 0
        self.completed: list[tuple[str, dict[str, object]]] = []
        self.failures: list[tuple[str, dict[str, object]]] = []
        self.requests: dict[str, int] = {}
        self.process = _RunProcessNamespace(self)

    def complete_external_filter(self, article_id: str, **kwargs: object) -> None:
        self.completed.append((article_id, dict(kwargs)))


def _run_external_filter(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> tuple[_RunAdapter, list[dict[str, object]]]:
    candidates = [
        _external_candidate(article_id=f"article-{index}") for index in range(3)
    ]
    adapter = _RunAdapter(candidates)
    settings = SimpleNamespace(
        external_filter_batch_size=10,
        external_filter_max_retries=3,
        external_filter_threshold=60,
        external_filter_negative_threshold=30,
        internal_filter_threshold=60,
        internal_filter_negative_threshold=30,
        default_concurrency=2,
    )
    prompts = {
        key: _prompt(key)
        for key in (
            "external_positive",
            "external_negative",
            "internal_positive",
            "internal_negative",
        )
    }
    alerts = _record_alerts(monkeypatch)

    def fail_call(candidate: ExternalFilterCandidate, **kwargs: object) -> str:
        del kwargs
        adapter.requests[candidate.article_id] = (
            adapter.requests.get(candidate.article_id, 0) + 1
        )
        raise error

    monkeypatch.setattr(external_filter, "get_adapter", lambda: adapter)
    monkeypatch.setattr(external_filter, "get_settings", lambda: settings)
    monkeypatch.setattr(external_filter, "load_active_prompts", lambda adapter: prompts)
    monkeypatch.setattr(external_filter, "call_external_filter_model", fail_call)

    external_filter.run(limit=None, concurrency=2)
    return adapter, alerts


@pytest.mark.parametrize(
    "error",
    [
        _systematic_request_error(),
        LLMEndpointBlockedError("接入点主机不在 LLM_ALLOWED_HOSTS 白名单内"),
        LLMQuotaError(
            operation="external_filter",
            model="model-a",
            status_code=402,
            response_text="Insufficient Balance",
        ),
    ],
    ids=["systematic", "blocked_host", "quota"],
)
def test_run_terminates_when_every_candidate_fails_systematically(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    adapter, alerts = _run_external_filter(monkeypatch, error)

    # 同轮去重：每篇只请求一次，run() 不会因为失败计数不涨而死循环。
    assert adapter.requests == {
        "article-0": 1,
        "article-1": 1,
        "article-2": 1,
    }
    assert adapter.fetch_calls == 2
    assert adapter.completed == []
    assert adapter.failures == []
    assert len(alerts) == (0 if isinstance(error, LLMQuotaError) else 1)
    if alerts:
        assert alerts[0]["operation"] == "external_filter"


def test_run_swallows_request_alert_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_alert(**kwargs: object) -> bool:
        raise RuntimeError("feishu down")

    monkeypatch.setattr(llm_failures, "notify_llm_request_alert", broken_alert)

    adapter, _ = _run_external_filter(monkeypatch, _systematic_request_error())

    assert adapter.requests == {
        "article-0": 1,
        "article-1": 1,
        "article-2": 1,
    }
    assert adapter.failures == []
