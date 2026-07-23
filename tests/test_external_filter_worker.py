from __future__ import annotations

from unittest.mock import patch

from src.adapters import external_filter_model
from src.domain.external_filter import ExternalFilterCandidate
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
        )
    mock_call.assert_called_once_with(candidate, category="internal_positive", retries=2)
    assert score == 88
    assert raw == "88"
    assert passed is True
    assert category == "internal_positive"
    assert prompt_key == "internal_positive"
    assert prompt_version == "v1"


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
        )
    assert score == 40
    assert passed is False
    assert category == "internal_positive"
    assert prompt_key == "internal_positive"
    assert prompt_version == "v1"


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
        )
    mock_call.assert_called_once_with(candidate, category="external_negative", retries=1)
    assert score == 40
    assert raw == "40"
    assert passed is True  # uses the lower negative threshold
    assert category == "external_negative"
    assert prompt_key == "external_negative"
    assert prompt_version == "v1"


def test_internal_prompt_includes_bonus_keywords():
    candidate = _external_candidate(keyword_matches=("北京教育改革", "首都治理"))
    with patch(
        "src.adapters.external_filter_model._load_prompt_template", return_value="PROMPT"
    ):
        prompt = external_filter_model.build_prompt(candidate, category="internal")
    assert "Bonus Keywords: 北京教育改革、首都治理" in prompt
    assert "PROMPT" in prompt
