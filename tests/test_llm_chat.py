from __future__ import annotations

import json
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.adapters import llm_chat
from src.adapters.llm_chat import (
    LLMQuotaError,
    LLMWallClockTimeout,
    apply_reasoning_config,
    extract_message_text,
    is_llm_quota_response,
    post_chat_completion,
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


class _StreamingResponse:
    encoding = "utf-8"

    def __init__(self, status_code: int, chunks) -> None:
        self.status_code = status_code
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


def test_post_chat_completion_stops_keepalive_stream_at_wall_clock_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _StreamingResponse(200, _keepalive_chunks())
    monkeypatch.setattr(llm_chat.requests, "post", lambda *args, **kwargs: response)

    started_at = time.monotonic()
    with pytest.raises(LLMWallClockTimeout):
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a", "messages": []},
            headers={"Authorization": "Bearer test"},
            timeout=1,
            budget=0.06,
            retries=1,
            retryable_statuses={429, 500},
            operation="test_keepalive",
            model="model-a",
        )
    elapsed = time.monotonic() - started_at

    assert 0.04 <= elapsed < 0.5
    assert response.closed is True


def test_post_chat_completion_stops_retries_when_backoff_uses_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return _StreamingResponse(503, [b"service unavailable"])

    monkeypatch.setattr(llm_chat.requests, "post", fake_post)

    started_at = time.monotonic()
    with pytest.raises(LLMWallClockTimeout):
        post_chat_completion(
            "https://llm.example.test/chat/completions",
            payload={"model": "model-a", "messages": []},
            headers={},
            timeout=1,
            budget=0.08,
            retries=10,
            retryable_statuses={503},
            operation="test_retries",
            model="model-a",
            backoff_initial=0.03,
        )
    elapsed = time.monotonic() - started_at

    assert len(calls) == 2
    assert 0.06 <= elapsed < 0.5
    assert all(call["stream"] is True for call in calls)
    assert all("stream" not in call["json"] for call in calls)


def test_post_chat_completion_returns_complete_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"choices": [{"message": {"content": "ok"}}], "provider": "test"}
    response = _StreamingResponse(200, [json.dumps(expected).encode("utf-8")])
    monkeypatch.setattr(llm_chat.requests, "post", lambda *args, **kwargs: response)

    result = post_chat_completion(
        "https://llm.example.test/chat/completions",
        payload={"model": "model-a"},
        headers={},
        timeout=1,
        budget=1,
        retries=1,
        retryable_statuses=set(),
        operation="test_success",
        model="model-a",
    )

    assert result == expected


def _keepalive_chunks():
    while True:
        time.sleep(0.01)
        yield b" "
