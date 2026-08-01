from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.adapters.llm_beijing_gate import (
    BeijingGateIndeterminateError,
    BeijingGateResponse,
)
from src.domain.external_filter import BeijingGateCandidate
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


class _DummyFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _DummyExecutor:
    def __init__(self, result_map):
        self._result_map = result_map

    def submit(self, func, candidate, retries):
        return _DummyFuture(self._result_map[candidate.article_id])


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
    adapter = MagicMock()
    executor = _DummyExecutor({candidate.article_id: decision})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        confirmed, rerouted, failures, fallbacks = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=1,
            max_failures=3,
        )

    assert (confirmed, rerouted, failures, fallbacks) == (1, 0, 0, 0)
    kwargs = adapter.complete_beijing_gate.call_args.kwargs
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
    adapter = MagicMock()
    executor = _DummyExecutor({candidate.article_id: decision})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        confirmed, rerouted, failures, fallbacks = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=1,
            max_failures=3,
        )

    assert (confirmed, rerouted, failures, fallbacks) == (0, 1, 0, 0)
    kwargs = adapter.complete_beijing_gate.call_args.kwargs
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
    adapter = MagicMock()
    executor = _DummyExecutor({candidate.article_id: error})

    with patch("src.workers.geo_classify.as_completed", lambda futures: list(futures)):
        confirmed, rerouted, failures, fallbacks = geo_classify._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=3,
            max_failures=3,
        )

    assert (confirmed, rerouted, failures, fallbacks) == (0, 0, 1, 0)
    kwargs = adapter.mark_beijing_gate_failure.call_args.kwargs
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
    adapter = MagicMock()
    adapter.fetch_beijing_gate_candidates.return_value = [candidate]
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

    assert result == (0, 0, 1, 0)
    assert adapter.fetch_beijing_gate_candidates.call_count == 2
    adapter.mark_beijing_gate_failure.assert_called_once()


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


class _RunAdapter:
    def __init__(self, candidate: BeijingGateCandidate) -> None:
        self.candidate = candidate
        self.local_updates: list[tuple[str, bool | None, str]] = []
        self.gate_updates: list[str] = []
        self.news_summaries = _NewsSummariesNamespace(self)

    def fetch_beijing_gate_candidates(
        self,
        limit: int,
        *,
        max_failures: int,
    ) -> list[BeijingGateCandidate]:
        return [self.candidate]

    def complete_beijing_gate(self, article_id: str, **kwargs: object) -> None:
        self.gate_updates.append(article_id)

    def mark_beijing_gate_failure(self, article_id: str, **kwargs: object) -> None:
        raise AssertionError(f"unexpected Beijing gate failure: {article_id}")


def test_run_combines_local_routing_and_beijing_gate(monkeypatch) -> None:
    candidate = _beijing_gate_candidate()
    adapter = _RunAdapter(candidate)
    settings = SimpleNamespace(
        process_limit=None,
        default_concurrency=2,
        external_filter_batch_size=10,
        beijing_gate_max_retries=1,
        beijing_keywords_path="keywords.txt",
    )
    decision = SimpleNamespace(
        is_beijing_related=True,
        reason="明确属于北京市范围",
        raw_text="raw text",
    )
    monkeypatch.setattr(geo_classify, "get_adapter", lambda: adapter)
    monkeypatch.setattr(geo_classify, "get_settings", lambda: settings)
    monkeypatch.setattr(geo_classify, "load_beijing_keywords", lambda path: ["北京"])
    monkeypatch.setattr(geo_classify, "call_beijing_gate", lambda candidate, retries: decision)

    geo_classify.run(limit=1, concurrency=2)

    assert adapter.local_updates == [
        (candidate.article_id, True, "pending_beijing_gate")
    ]
    assert adapter.gate_updates == [candidate.article_id]
