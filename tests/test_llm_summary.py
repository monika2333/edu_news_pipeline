from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from src.adapters import llm_chat
from src.adapters.llm_chat import LLMQuotaError
from src.adapters.llm_summary import build_summary_payload, summarise
from src.config import get_settings


def test_build_summary_payload_requests_around_200_chinese_chars() -> None:
    payload = build_summary_payload(
        {
            "title": "测试标题",
            "content": "这是一段用于测试摘要提示词的正文。",
        }
    )

    message = payload["messages"][0]["content"]
    assert "约200字" in message
    assert "一段" in message
    assert "不要分条" in message
    assert "不要添加原文没有的信息" in message
    assert "测试标题" in message


def test_build_summary_payload_requires_content() -> None:
    with pytest.raises(ValueError, match="Article content is required"):
        build_summary_payload({"title": "无正文"})


def test_summarise_raises_quota_error_without_retry(monkeypatch, tmp_path) -> None:
    settings = replace(
        get_settings(),
        llm_api_key="test-key",
        llm_summary_model="model-summary",
        llm_quota_alert_state_path=tmp_path / "quota_state.json",
    )
    calls = []

    class _Response:
        status_code = 402
        text = "insufficient credits"

    monkeypatch.setattr(llm_chat, "get_settings", lambda: settings)
    monkeypatch.setattr("src.notifications.feishu.notify_llm_quota_alert", lambda **kwargs: calls.append(kwargs) or True)

    with patch("src.adapters.llm_summary.get_settings", return_value=settings), patch(
        "src.adapters.llm_summary.requests.post",
        return_value=_Response(),
    ) as post:
        with pytest.raises(LLMQuotaError):
            summarise({"title": "测试标题", "content": "正文内容"}, retries=3)

    assert post.call_count == 1
    assert calls[0]["operation"] == "summarize"
