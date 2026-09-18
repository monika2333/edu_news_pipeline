from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from src.adapters import llm_chat, llm_duplicate_review, llm_endpoint, llm_scoring, llm_summary
from src.adapters.llm_chat import (
    LLMEndpointBlockedError,
    LLMQuotaError,
    apply_reasoning_config,
    build_headers,
)
from src.adapters.llm_endpoint import LLMEndpointKeyMissingError
from src.business_config import LLMEndpointConfig
from src.config import get_settings
from conftest import (
    DEEPSEEK_ENDPOINT,
    OPENROUTER_ENDPOINT,
    make_endpoint_config,
)


class _RawStream:
    def __init__(self, chunks) -> None:
        self._chunks = iter(chunks)

    def read1(self, amount: int, *, decode_content: bool) -> bytes:
        assert decode_content is True
        return next(self._chunks, b"")


class _StubResponse:
    encoding = "utf-8"

    def __init__(self, body: dict[str, Any]) -> None:
        self.status_code = 200
        self.raw = _RawStream([json.dumps(body).encode("utf-8")])
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _completion(content: str = "ok") -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def post_capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake_post(url, *, json=None, headers=None, **kwargs):
        calls.append({"url": url, "json": json, "headers": dict(headers), **kwargs})
        # 内容取查重可解析的合法响应；摘要/评分路径只把它当文本
        return _StubResponse(_completion('{"duplicate_groups":[]}'))

    monkeypatch.setattr(llm_chat.requests, "post", fake_post)
    return calls


@pytest.fixture(autouse=True)
def _api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "deepseek-key")


@pytest.fixture(autouse=True)
def _console_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(
        get_settings(),
        llm_api_http_referer="https://console.example.test",
        llm_api_title="Edu News Pipeline",
        llm_reasoning_effort=None,
        llm_reasoning_max_tokens=None,
        llm_reasoning_exclude=True,
    )
    monkeypatch.setattr(llm_endpoint, "get_settings", lambda: settings)
    for module in (llm_summary, llm_scoring, llm_duplicate_review):
        monkeypatch.setattr(module, "get_settings", lambda s=settings: s)


def test_null_endpoint_reproduces_legacy_request_byte_for_byte(
    openrouter_context,
    post_capture: list[dict[str, Any]],
) -> None:
    settings = llm_summary.get_settings()
    expected_headers = build_headers(
        api_key="openrouter-key",
        referer=settings.llm_api_http_referer,
        title=settings.llm_api_title,
    )
    expected_payload = llm_summary.build_summary_payload(
        {"title": "标题", "content": "正文"}
    )
    expected_payload["model"] = "summary-model"
    expected_payload["temperature"] = 0.2
    apply_reasoning_config(
        expected_payload,
        settings=settings,
        enabled=True,
    )

    with openrouter_context():
        llm_summary.summarise({"title": "标题", "content": "正文"})

    assert len(post_capture) == 1
    call = post_capture[0]
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"] == expected_headers
    assert call["json"] == expected_payload


def test_duplicate_review_on_thinking_endpoint_switches_url_key_and_body(
    monkeypatch: pytest.MonkeyPatch,
    post_capture: list[dict[str, Any]],
) -> None:
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, DEEPSEEK_ENDPOINT),
        step_endpoints={"duplicate_review": "deepseek"},
    )
    responses = iter([
        {"choices": [{"message": {"content": '{"duplicate_groups":[]}'}}]},
        _completion("90"),
    ])

    def scripted_post(url, *, json=None, headers=None, **kwargs):
        post_capture.append(
            {"url": url, "json": json, "headers": dict(headers), **kwargs}
        )
        return _StubResponse(next(responses))

    monkeypatch.setattr(llm_chat.requests, "post", scripted_post)

    from src.business_config import business_config_context

    items = [{"article_id": "a1", "title": "标题", "summary": "摘要", "source": "来源"}]
    with business_config_context(config):
        groups = llm_duplicate_review.call_duplicate_review(items)
        score_raw = llm_scoring.call_relevance_api("正文")

    assert groups == []
    assert score_raw == "90"
    duplicate_call, scoring_call = post_capture[0], post_capture[1]
    # 查重复核走 DeepSeek thinking 接入点
    assert duplicate_call["url"] == "https://api.deepseek.com/chat/completions"
    assert duplicate_call["headers"]["Authorization"] == "Bearer deepseek-key"
    assert "HTTP-Referer" not in duplicate_call["headers"]
    assert "X-Title" not in duplicate_call["headers"]
    assert duplicate_call["json"]["thinking"] == {"type": "enabled"}
    assert "reasoning" not in duplicate_call["json"]
    # 其余步骤不受影响：评分仍走默认 OpenRouter 接入点
    assert scoring_call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert scoring_call["headers"]["Authorization"] == "Bearer openrouter-key"
    assert scoring_call["headers"]["HTTP-Referer"] == "https://console.example.test"
    assert scoring_call["headers"]["X-Title"] == "Edu News Pipeline"
    assert "reasoning" in scoring_call["json"]
    assert "thinking" not in scoring_call["json"]


def test_thinking_endpoint_explicitly_disables_thinking(
    post_capture: list[dict[str, Any]],
) -> None:
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, DEEPSEEK_ENDPOINT),
        step_endpoints={"duplicate_review": "deepseek"},
    )
    config = replace(
        config,
        llm_steps={
            **config.llm_steps,
            "duplicate_review": replace(config.llm_steps["duplicate_review"], reasoning=False),
        },
    )
    from src.business_config import business_config_context

    with business_config_context(config):
        llm_duplicate_review.call_duplicate_review([])

    payload = post_capture[0]["json"]
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning" not in payload


def test_thinking_endpoint_passes_reasoning_effort_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    post_capture: list[dict[str, Any]],
) -> None:
    settings = replace(llm_duplicate_review.get_settings(), llm_reasoning_effort="high")
    monkeypatch.setattr(llm_endpoint, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_duplicate_review, "get_settings", lambda: settings)
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, DEEPSEEK_ENDPOINT),
        step_endpoints={"duplicate_review": "deepseek"},
    )
    from src.business_config import business_config_context

    with business_config_context(config):
        llm_duplicate_review.call_duplicate_review([])

    payload = post_capture[0]["json"]
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert "max_tokens" not in payload["thinking"]
    assert "exclude" not in payload["thinking"]


def test_temperature_override_rewrites_assembled_body(
    post_capture: list[dict[str, Any]],
) -> None:
    override = LLMEndpointConfig(
        key="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com",
        api_key_env="LLM_DEEPSEEK_API_KEY",
        api_style="thinking",
        temperature_override=0.7,
    )
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, override),
        step_endpoints={"duplicate_review": "deepseek"},
    )
    from src.business_config import business_config_context

    with business_config_context(config):
        llm_duplicate_review.call_duplicate_review([])

    # call_duplicate_review 组装时写死 temperature 0.0，接入点覆盖必须生效
    assert post_capture[0]["json"]["temperature"] == 0.7


def test_missing_endpoint_key_names_the_env_variable(
    monkeypatch: pytest.MonkeyPatch,
    openrouter_context,
    post_capture: list[dict[str, Any]],
) -> None:
    orphan = LLMEndpointConfig(
        key="ghost",
        label="Ghost",
        base_url="https://api.deepseek.com",
        api_key_env="LLM_GHOST_API_KEY",
        api_style="thinking",
        temperature_override=None,
    )
    monkeypatch.delenv("LLM_GHOST_API_KEY", raising=False)
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, orphan),
        step_endpoints={"scoring": "ghost"},
    )
    from src.business_config import business_config_context

    with business_config_context(config):
        with pytest.raises(LLMEndpointKeyMissingError, match="LLM_GHOST_API_KEY"):
            llm_scoring.score_text("正文")

    assert post_capture == []


def test_non_whitelisted_host_is_blocked_before_sending(
    openrouter_context,
    post_capture: list[dict[str, Any]],
) -> None:
    evil = LLMEndpointConfig(
        key="evil",
        label="Evil",
        base_url="https://evil.example.com/v1",
        api_key_env="LLM_API_KEY",
        api_style="openrouter",
        temperature_override=None,
    )
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, evil),
        step_endpoints={"scoring": "evil"},
    )
    from src.business_config import business_config_context

    with business_config_context(config):
        with pytest.raises(LLMEndpointBlockedError, match="evil.example.com"):
            llm_scoring.score_text("正文")

    assert post_capture == []


def test_snapshot_records_endpoint_mapping_without_credentials(
    openrouter_context,
) -> None:
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, DEEPSEEK_ENDPOINT),
        step_endpoints={"duplicate_review": "deepseek"},
    )

    snapshot = config.snapshot()
    serialized = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["llm_models"]["duplicate_review"]["endpoint"] == "deepseek"
    assert snapshot["llm_models"]["summary"]["endpoint"] == "openrouter"
    assert snapshot["llm_endpoints"] == {
        "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_style": "openrouter"},
        "deepseek": {"base_url": "https://api.deepseek.com", "api_style": "thinking"},
    }
    assert "api_key_env" not in serialized
    assert "LLM_API_KEY" not in serialized
    assert "openrouter-key" not in serialized
    assert "deepseek-key" not in serialized


def test_quota_error_carries_endpoint_label_into_alert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls: list[dict[str, Any]] = []
    settings = replace(
        llm_chat.get_settings(),
        llm_quota_alert_enabled=True,
        llm_quota_alert_cooldown_seconds=3600,
        llm_quota_alert_state_path=tmp_path / "state.json",
    )
    monkeypatch.setattr(llm_chat, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "src.notifications.feishu.notify_llm_quota_alert",
        lambda **kwargs: calls.append(kwargs) or True,
    )
    monkeypatch.setattr(llm_chat.time, "time", lambda: 1000.0)

    with pytest.raises(LLMQuotaError):
        llm_chat.raise_for_llm_quota_error(
            status_code=402,
            response_text="Insufficient Balance",
            operation="duplicate_review",
            model="deepseek-chat",
            endpoint_label="DeepSeek",
        )

    assert "DeepSeek" in calls[0]["operation"]
    assert "接入点" in calls[0]["operation"]
    assert calls[0]["operation"].startswith("duplicate_review")
