from types import SimpleNamespace

import pytest

from src.notifications import feishu


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(feishu, "_token_cache", None)
    yield
    monkeypatch.setattr(feishu, "_token_cache", None)


@pytest.fixture
def fake_settings(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        feishu_app_id="app",
        feishu_app_secret="secret",
        feishu_receive_id="openid",
        feishu_receive_id_type="open_id",
        llm_request_alert_enabled=True,
        llm_request_alert_cooldown_seconds=1800,
        llm_request_alert_state_path=tmp_path / "llm_request_alert_state.json",
    )
    monkeypatch.setattr(feishu, "get_settings", lambda: settings)
    return settings


def _request_alert_kwargs(**overrides):
    payload = dict(
        operation="beijing_gate",
        endpoint_label="DeepSeek",
        model="deepseek-flash",
        status_code=400,
        provider_code="invalid_request_error",
        provider_message="This response_format type is unavailable now",
        request_id="req-123",
    )
    payload.update(overrides)
    return payload


def test_notify_llm_quota_alert_builds_text_message(monkeypatch, fake_settings):
    captured = {}

    def fake_send(config, message):
        captured["config"] = config
        captured["message"] = message

    monkeypatch.setattr(feishu, "_send_text_message", fake_send)

    result = feishu.notify_llm_quota_alert(
        operation="score",
        model="model-a",
        status_code=402,
        response_text="insufficient credits",
    )

    assert result is True
    assert captured["config"].receive_id == "openid"
    assert "LLM quota/billing alert" in captured["message"]
    assert "Operation: score" in captured["message"]
    assert "Model: model-a" in captured["message"]
    assert "HTTP status: 402" in captured["message"]
    assert "insufficient credits" in captured["message"]


def test_notify_llm_quota_alert_missing_config(monkeypatch):
    empty_settings = SimpleNamespace(
        feishu_app_id=None,
        feishu_app_secret=None,
        feishu_receive_id=None,
        feishu_receive_id_type="open_id",
    )
    monkeypatch.setattr(feishu, "get_settings", lambda: empty_settings)

    with pytest.raises(feishu.FeishuConfigError):
        feishu.notify_llm_quota_alert(
            operation="score",
            model="model-a",
            status_code=402,
            response_text="insufficient credits",
        )


def test_notify_console_registration_builds_text_message(
    monkeypatch,
    fake_settings,
):
    captured = {}

    def fake_send(config, message):
        captured["config"] = config
        captured["message"] = message

    monkeypatch.setattr(feishu, "_send_text_message", fake_send)

    result = feishu.notify_console_registration(
        username="zhangming",
        display_name="张明",
        preferred_weekday=3,
    )

    assert result is True
    assert captured["config"].receive_id == "openid"
    assert "新用户注册" in captured["message"]
    assert "姓名：张明" in captured["message"]
    assert "用户名：zhangming" in captured["message"]
    assert "首选值班日：周四" in captured["message"]
    assert "尚未自动排班" in captured["message"]


def test_send_text_to_chat_uses_chat_id(monkeypatch, fake_settings):
    captured = {}

    def fake_send(config, message):
        captured["config"] = config
        captured["message"] = message

    monkeypatch.setattr(feishu, "_send_text_message", fake_send)

    assert feishu.send_text_to_chat(chat_id="oc_chat", message="存档成功") is True
    assert captured["config"].receive_id == "oc_chat"
    assert captured["config"].receive_id_type == "chat_id"
    assert captured["message"] == "存档成功"


def test_notify_llm_request_alert_builds_text_message(monkeypatch, fake_settings):
    captured = {}

    def fake_send(config, message):
        captured["config"] = config
        captured["message"] = message

    monkeypatch.setattr(feishu, "_send_text_message", fake_send)

    result = feishu.notify_llm_request_alert(**_request_alert_kwargs())

    assert result is True
    assert captured["config"].receive_id == "openid"
    message = captured["message"]
    for fragment in (
        "LLM request rejected",
        "Operation: beijing_gate",
        "Endpoint: DeepSeek",
        "Model: deepseek-flash",
        "HTTP status: 400",
        "Provider code: invalid_request_error",
        "Request id: req-123",
        "This response_format type is unavailable now",
        "Occurred at:",
        "Action:",
    ):
        assert fragment in message
    assert fake_settings.llm_request_alert_state_path.exists()


def test_notify_llm_request_alert_suppresses_same_fingerprint(monkeypatch, fake_settings):
    sent = []
    monkeypatch.setattr(
        feishu,
        "_send_text_message",
        lambda config, message: sent.append(message),
    )

    first = feishu.notify_llm_request_alert(**_request_alert_kwargs())
    # 同一错误换个 request_id 仍是同一指纹，冷却窗口内不再发。
    second = feishu.notify_llm_request_alert(
        **_request_alert_kwargs(
            request_id="req-456",
            provider_message=(
                "This response_format type is unavailable now "
                "(request_id: req-456)"
            ),
        )
    )

    assert first is True
    assert second is False
    assert len(sent) == 1


def test_notify_llm_request_alert_sends_different_fingerprints(
    monkeypatch,
    fake_settings,
):
    sent = []
    monkeypatch.setattr(
        feishu,
        "_send_text_message",
        lambda config, message: sent.append(message),
    )

    assert feishu.notify_llm_request_alert(**_request_alert_kwargs()) is True
    assert (
        feishu.notify_llm_request_alert(
            **_request_alert_kwargs(
                status_code=401,
                provider_code="invalid_api_key",
                provider_message="Authentication failed",
            )
        )
        is True
    )
    assert len(sent) == 2


def test_notify_llm_request_alert_swallows_delivery_errors(
    monkeypatch,
    fake_settings,
):
    def boom(config, message):
        raise feishu.FeishuRequestError("network down")

    monkeypatch.setattr(feishu, "_send_text_message", boom)

    assert feishu.notify_llm_request_alert(**_request_alert_kwargs()) is False
    # 发送失败不写状态，修好网络后同一指纹仍能补发。
    assert not fake_settings.llm_request_alert_state_path.exists()


def test_notify_llm_request_alert_swallows_unexpected_errors(
    monkeypatch,
    fake_settings,
):
    def boom(config, message):
        raise ValueError("boom")

    monkeypatch.setattr(feishu, "_send_text_message", boom)

    assert feishu.notify_llm_request_alert(**_request_alert_kwargs()) is False


def test_notify_llm_request_alert_missing_config_is_swallowed(
    monkeypatch,
    tmp_path,
):
    settings = SimpleNamespace(
        feishu_app_id=None,
        feishu_app_secret=None,
        feishu_receive_id=None,
        feishu_receive_id_type="open_id",
        llm_request_alert_enabled=True,
        llm_request_alert_cooldown_seconds=1800,
        llm_request_alert_state_path=tmp_path / "state.json",
    )
    monkeypatch.setattr(feishu, "get_settings", lambda: settings)

    assert feishu.notify_llm_request_alert(**_request_alert_kwargs()) is False


def test_notify_llm_request_alert_respects_disabled_flag(monkeypatch, fake_settings):
    sent = []
    fake_settings.llm_request_alert_enabled = False
    monkeypatch.setattr(
        feishu,
        "_send_text_message",
        lambda config, message: sent.append(message),
    )

    assert feishu.notify_llm_request_alert(**_request_alert_kwargs()) is False
    assert sent == []
