from __future__ import annotations

from dataclasses import replace

from src.adapters.llm_chat import apply_reasoning_config, extract_message_text
from src.config import get_settings


def test_apply_reasoning_config_uses_reasoning_field():
    settings = replace(
        get_settings(),
        llm_reasoning_effort="high",
        llm_reasoning_max_tokens=None,
        llm_reasoning_exclude=True,
    )
    payload = {}

    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=True,
    )

    assert payload == {
        "reasoning": {"enabled": True, "effort": "high", "exclude": True}
    }


def test_apply_reasoning_config_does_nothing_when_disabled():
    settings = get_settings()
    payload = {}

    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=False,
    )

    assert payload == {}


def test_extract_message_text_reads_message_reasoning():
    choice = {"message": {"content": "", "reasoning": "42"}}

    assert extract_message_text(choice) == "42"


def test_extract_message_text_reads_legacy_reasoning_content():
    choice = {"message": {"content": ""}, "reasoning_content": ["4", "2"]}

    assert extract_message_text(choice) == "4 2"
