from __future__ import annotations

import pytest

from src.adapters.llm_summary import build_summary_payload


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
