from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.adapters import llm_chat
from src.adapters.llm_chat import (
    LLMQuotaError,
    apply_reasoning_config,
    extract_message_text,
    is_llm_quota_response,
    raise_for_llm_quota_error,
)
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


def test_is_llm_quota_response_detects_billing_and_balance_errors():
    assert is_llm_quota_response(402, "Payment required")
    assert is_llm_quota_response(429, "insufficient credits for this request")
    assert is_llm_quota_response(403, "账户余额不足，请充值")


def test_is_llm_quota_response_ignores_plain_rate_limit():
    assert not is_llm_quota_response(429, "rate limit exceeded, retry later")


def test_raise_for_llm_quota_error_sends_alert_once_per_cooldown(monkeypatch, tmp_path):
    calls = []
    settings = SimpleNamespace(
        llm_quota_alert_enabled=True,
        llm_quota_alert_cooldown_seconds=21600,
        llm_quota_alert_state_path=tmp_path / "state.json",
    )

    def fake_notify(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(llm_chat, "get_settings", lambda: settings)
    monkeypatch.setattr("src.notifications.feishu.notify_llm_quota_alert", fake_notify)
    monkeypatch.setattr(llm_chat.time, "time", lambda: 1000.0)

    with pytest.raises(LLMQuotaError) as first:
        raise_for_llm_quota_error(
            status_code=429,
            response_text="insufficient credits for this request",
            operation="score",
            model="model-a",
        )

    with pytest.raises(LLMQuotaError):
        raise_for_llm_quota_error(
            status_code=429,
            response_text="insufficient credits for this request",
            operation="summarize",
            model="model-a",
        )

    assert first.value.operation == "score"
    assert len(calls) == 1
    assert calls[0]["operation"] == "score"
    assert settings.llm_quota_alert_state_path.exists()


def test_raise_for_llm_quota_error_allows_normal_429():
    raise_for_llm_quota_error(
        status_code=429,
        response_text="rate limit exceeded",
        operation="score",
        model="model-a",
    )
