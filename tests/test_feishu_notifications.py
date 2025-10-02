import pathlib
from types import SimpleNamespace

import pytest

from src.notifications import feishu


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch):
    monkeypatch.setattr(feishu, "_token_cache", None)
    yield
    monkeypatch.setattr(feishu, "_token_cache", None)


def test_notify_export_summary_builds_message(monkeypatch):
    captured = {}
    settings = SimpleNamespace(
        feishu_app_id="app",
        feishu_app_secret="secret",
        feishu_receive_id="openid",
        feishu_receive_id_type="open_id",
    )
    monkeypatch.setattr(feishu, "get_settings", lambda: settings)

    def fake_send(config, message):
        captured["config"] = config
        captured["message"] = message

    monkeypatch.setattr(feishu, "_send_text_message", fake_send)

    result = feishu.notify_export_summary(
        tag="2025-10-02",
        output_path=pathlib.Path("/tmp/demo.txt"),
        entries=["标题\n摘要内容"],
        category_counts={"高校": 2, "其他": 0},
    )

    assert result is True
    assert "Edu News Brief - 2025-10-02" in captured["message"]
    assert "高校:2" in captured["message"]
    assert captured["config"].receive_id == "openid"


def test_notify_export_summary_missing_config(monkeypatch):
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
            output_path=pathlib.Path("/tmp/demo.txt"),
            entries=["item"],
            category_counts={},
        )
