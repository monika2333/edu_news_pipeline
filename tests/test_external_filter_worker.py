from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.adapters import external_filter_model
from src.domain.external_filter import BeijingGateCandidate, ExternalFilterCandidate
from src.workers import external_filter


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


def test_score_candidate_uses_internal_threshold_and_category():
    candidate = _external_candidate()
    thresholds = {"external": 30, "internal": 60}
    with patch(
        "src.workers.external_filter.call_external_filter_model", return_value="88"
    ) as mock_call:
        score, raw, passed, category = external_filter._score_candidate(
            candidate,
            retries=2,
            thresholds=thresholds,
        )
    mock_call.assert_called_once_with(candidate, category="internal", retries=2)
    assert score == 88
    assert raw == "88"
    assert passed is True
    assert category == "internal"


def test_score_candidate_respects_internal_threshold():
    candidate = _external_candidate()
    thresholds = {"external": 30, "internal": 60}
    with patch(
        "src.workers.external_filter.call_external_filter_model", return_value="40"
    ):
        score, raw, passed, category = external_filter._score_candidate(
            candidate,
            retries=1,
            thresholds=thresholds,
        )
    assert score == 40
    assert passed is False
    assert category == "internal"


class _DummyFuture:
    def __init__(self, result):
        self._result = result

    def result(self):
        return self._result


class _DummyExecutor:
    def __init__(self, result_map):
        self._result_map = result_map
        self.submitted = []

    def submit(self, func, candidate, retries):
        # record submission for assertion if needed
        self.submitted.append((func, candidate, retries))
        decision = self._result_map.get(candidate.article_id)
        return _DummyFuture(decision)


def test_process_beijing_gate_passes_internal_category():
    candidate = _beijing_gate_candidate()
    decision = SimpleNamespace(
        is_beijing_related=True,
        reason="明确属于北京市范围",
        raw_text="raw text",
    )
    adapter = MagicMock()
    executor = _DummyExecutor({candidate.article_id: decision})

    with patch(
        "src.workers.external_filter.as_completed", lambda futures: list(futures)
    ):
        confirmed, rerouted, failures = external_filter._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=1,
            max_failures=3,
        )

    assert confirmed == 1
    assert rerouted == 0
    assert failures == 0
    adapter.complete_beijing_gate.assert_called_once()
    kwargs = adapter.complete_beijing_gate.call_args.kwargs
    assert kwargs["candidate_category"] == "internal"
    assert kwargs["sentiment_label"] == "positive"
    assert kwargs["status"] == "ready_for_export"


def test_process_beijing_gate_reroutes_external_category():
    candidate = _beijing_gate_candidate(is_beijing_related=False)
    decision = SimpleNamespace(
        is_beijing_related=False,
        reason="判定为外省内容",
        raw_text="raw text",
    )
    adapter = MagicMock()
    executor = _DummyExecutor({candidate.article_id: decision})

    with patch(
        "src.workers.external_filter.as_completed", lambda futures: list(futures)
    ):
        confirmed, rerouted, failures = external_filter._process_beijing_gate(
            adapter,
            [candidate],
            executor,
            llm_retries=1,
            max_failures=3,
        )

    assert confirmed == 0
    assert rerouted == 1
    assert failures == 0
    adapter.complete_beijing_gate.assert_called_once()
    kwargs = adapter.complete_beijing_gate.call_args.kwargs
    assert kwargs["candidate_category"] == "external"
    assert kwargs["status"] == "pending_external_filter"
    assert kwargs["reset_external_filter"] is True


def test_internal_prompt_includes_bonus_keywords():
    candidate = _external_candidate(keyword_matches=("北京教育改革", "首都治理"))
    with patch(
        "src.adapters.external_filter_model._load_prompt_template", return_value="PROMPT"
    ):
        prompt = external_filter_model.build_prompt(candidate, category="internal")
    assert "Bonus Keywords: 北京教育改革、首都治理" in prompt
    assert "PROMPT" in prompt
