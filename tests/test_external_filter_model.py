from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

from src.adapters import external_filter_model as model
from src.config import get_settings
from src.domain.external_filter import ExternalFilterCandidate


def test_prompt_key_for_category_variants():
    assert model._prompt_key_for_category("internal_positive") == "internal"
    assert model._prompt_key_for_category("internal_negative") == "internal_negative"
    assert model._prompt_key_for_category("external_negative") == "external_negative"
    assert model._prompt_key_for_category("external_positive") == "external"
    assert model._prompt_key_for_category(None) == "external"


def _candidate(**overrides) -> ExternalFilterCandidate:
    base = dict(
        article_id="article-1",
        title="案例标题",
        source="案例来源",
        publish_time_iso=None,
        summary="摘要内容",
        content="正文内容",
        sentiment_label="negative",
        is_beijing_related=True,
        is_beijing_related_llm=None,
        external_importance_status="pending_external_filter",
        external_filter_fail_count=0,
        keyword_matches=("关键词A", "关键词B"),
    )
    base.update(overrides)
    return ExternalFilterCandidate(**base)


def test_build_prompt_internal_negative_includes_keywords():
    candidate = _candidate()
    with patch(
        "src.adapters.external_filter_model._load_prompt_template",
        return_value="PROMPT",
    ):
        prompt = model.build_prompt(candidate, category="internal_negative")
    assert "Bonus Keywords" in prompt
    assert "关键词A" in prompt
    assert "PROMPT" in prompt


def test_build_prompt_external_negative_skips_keyword_section():
    candidate = _candidate(is_beijing_related=False, keyword_matches=("A", "B"))
    with patch(
        "src.adapters.external_filter_model._load_prompt_template",
        return_value="PROMPT",
    ):
        prompt = model.build_prompt(candidate, category="external_negative")
    assert "Bonus Keywords" not in prompt
    assert "PROMPT" in prompt


def test_call_external_filter_model_sends_openrouter_reasoning_payload():
    candidate = _candidate()
    settings = replace(
        get_settings(),
        llm_api_key="test-key",
        llm_base_url="https://openrouter.ai/api/v1",
        external_filter_model_name="deepseek/deepseek-v4-flash",
        llm_reasoning_enabled=True,
        llm_reasoning_effort="high",
        llm_reasoning_max_tokens=None,
        llm_reasoning_exclude=True,
    )

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "80"}}]}

    with patch("src.adapters.external_filter_model.get_settings", return_value=settings), patch(
        "src.adapters.external_filter_model._load_prompt_template",
        return_value="PROMPT",
    ), patch("src.adapters.external_filter_model.requests.post", return_value=_Response()) as post:
        assert model.call_external_filter_model(candidate, category="internal_positive") == "80"

    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "deepseek/deepseek-v4-flash"
    assert payload["reasoning"] == {
        "enabled": True,
        "effort": "high",
        "exclude": True,
    }
    assert "enable_thinking" not in payload
