import pathlib
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
    demo_file = tmp_path / "demo.txt"
    demo_file.write_text("demo", encoding="utf-8")
    settings = SimpleNamespace(
        feishu_app_id="app",
        feishu_app_secret="secret",
        feishu_receive_id="openid",
        feishu_receive_id_type="open_id",
    )
    monkeypatch.setattr(feishu, "get_settings", lambda: settings)
    return demo_file


def test_notify_export_summary_builds_message(monkeypatch, fake_settings):
    captured = {}

    def fake_send(config, message):
        captured["config"] = config
        captured["message"] = message

    def fake_send_file(config, file_key):
        captured["file_key"] = file_key

    monkeypatch.setattr(feishu, "_send_text_message", fake_send)
    monkeypatch.setattr(feishu, "_send_file_message", fake_send_file)
    monkeypatch.setattr(feishu, "_upload_file", lambda config, path: "file_test_key")

    result = feishu.notify_export_summary(
        tag="2025-10-02",
        output_path=fake_settings,
        entries=["标题\n摘要内容"],
        category_counts={"高校": 2, "其他": 0},
    )

    assert result is True
    assert "Edu News Brief - 2025-10-02" in captured["message"]
    assert "高校:2" in captured["message"]
    assert captured["config"].receive_id == "openid"
    assert captured["file_key"] == "file_test_key"


def test_notify_export_summary_missing_config(monkeypatch, tmp_path):
    empty_settings = SimpleNamespace(
        feishu_app_id=None,
        feishu_app_secret=None,
        feishu_receive_id=None,
        feishu_receive_id_type="open_id",
    )
    monkeypatch.setattr(feishu, "get_settings", lambda: empty_settings)

    with pytest.raises(feishu.FeishuConfigError):
        feishu.notify_export_summary(
            tag="2025-10-02",
            output_path=tmp_path / "demo.txt",
            entries=["item"],
            category_counts={},
        )


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
