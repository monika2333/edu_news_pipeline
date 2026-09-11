from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from src.adapters import (
    external_filter_model,
    llm_beijing_gate,
    llm_duplicate_review,
    llm_scoring,
    llm_source,
    llm_summary,
    sentiment_classifier,
)
from src.adapters.llm_chat import apply_reasoning_config
from src.business_config import LLMStepConfig
from src.config import get_settings


class _PayloadCaptured(RuntimeError):
    pass


CASES: tuple[tuple[Any, str, Callable[[Any], Any]], ...] = (
    (
        llm_summary,
        "summary",
        lambda module: module.summarise(
            {"title": "标题", "content": "正文"},
            retries=1,
        ),
    ),
    (
        llm_source,
        "source",
        lambda module: module.detect_source(
            {"title": "标题", "content": "正文"},
            retries=1,
        ),
    ),
    (
        sentiment_classifier,
        "sentiment",
        lambda module: module.classify_sentiment("正文", retries=1),
    ),
    (
        llm_scoring,
        "scoring",
        lambda module: module.call_relevance_api("正文", retries=1),
    ),
    (
        external_filter_model,
        "external_filter",
        lambda module: module.call_external_filter_model(
            SimpleNamespace(),
            retries=1,
        ),
    ),
    (
        llm_beijing_gate,
        "beijing_gate",
        lambda module: module.call_beijing_gate(SimpleNamespace(), retries=1),
    ),
    (
        llm_duplicate_review,
        "duplicate_review",
        lambda module: module.call_duplicate_review([], retries=1),
    ),
)


@pytest.mark.parametrize(
    ("module", "step", "invoke"),
    CASES,
    ids=[case[1] for case in CASES],
)
@pytest.mark.parametrize("reasoning", [True, False], ids=["on", "off"])
def test_m6_each_step_applies_its_own_reasoning_to_request_payload(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    step: str,
    invoke: Callable[[Any], Any],
    reasoning: bool,
) -> None:
    settings = replace(get_settings(), llm_api_key="test-key")
    captured: dict[str, Any] = {}

    def configured(requested_step: str) -> LLMStepConfig:
        assert requested_step == step
        return LLMStepConfig(model=f"{step}-model", reasoning=reasoning)

    def capture_payload(
        payload: dict[str, Any],
        *,
        settings: Any,
        enabled: bool,
    ) -> None:
        apply_reasoning_config(payload, settings=settings, enabled=enabled)
        captured.update(payload)
        raise _PayloadCaptured

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "get_llm_step_config", configured)
    monkeypatch.setattr(module, "apply_reasoning_config", capture_payload)
    if module in {external_filter_model, llm_beijing_gate}:
        monkeypatch.setattr(module, "build_prompt", lambda *_args, **_kwargs: "prompt")

    with pytest.raises(_PayloadCaptured):
        invoke(module)

    assert captured["model"] == f"{step}-model"
    assert ("reasoning" in captured) is reasoning
