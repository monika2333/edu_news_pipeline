from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest

from src.adapters import llm_source
from src.adapters.llm_source import MAX_LLM_SOURCE_LENGTH, build_source_payload


def _completion_result(content: str, *, attempt: int = 1):
    raw = {
        "id": "completion-source",
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 10},
    }

    def fake_post(*args, attempt_callback=None, **kwargs):
        if attempt_callback is not None:
            attempt_callback(attempt)
        return raw

    return raw, fake_post


def test_build_source_payload_prioritizes_page_source_over_reported_by() -> None:
    payload = build_source_payload(
        {
            "title": "测试标题",
            "content": "中国新闻周刊报道，事件引发关注。\n\n来源：中国青年报",
        }
    )

    message = payload["messages"][0]["content"]
    assert "发布/署名媒体" in message
    assert "来源：" in message
    assert "某媒体报道" in message
    assert "不要优先当作整篇文章的发布媒体" in message
    assert "仅返回媒体名称本身" in message


def test_build_source_payload_requires_content() -> None:
    with pytest.raises(ValueError, match="Article content is required"):
        build_source_payload({"title": "无正文"})


def test_detect_source_returns_none_for_unknown_response() -> None:
    _, fake_post = _completion_result("未知")

    article = {"title": "测试标题", "content": "正文内容"}
    with patch("src.adapters.llm_source.post_chat_completion", side_effect=fake_post):
        result = llm_source.detect_source(article, retries=1)

    assert result["llm_source"] is None


def test_detect_source_uses_source_reasoning_setting() -> None:
    raw, fake_post = _completion_result("测试媒体")

    article = {"title": "测试标题", "content": "正文内容"}
    with patch(
        "src.adapters.llm_source.post_chat_completion",
        side_effect=fake_post,
    ) as post:
        result = llm_source.detect_source(article, retries=1)

    payload = post.call_args.kwargs["payload"]
    assert result["llm_source"] == "测试媒体"
    assert result["source_guard_discarded_length"] is None
    assert result["source_guard_triggered_attempt"] == 0
    assert result["raw"] is raw
    assert payload["reasoning"]["enabled"] is True


@pytest.mark.parametrize(
    ("source_length", "expected_source", "expected_discarded_length"),
    [
        (MAX_LLM_SOURCE_LENGTH, "x" * MAX_LLM_SOURCE_LENGTH, None),
        (MAX_LLM_SOURCE_LENGTH + 1, None, MAX_LLM_SOURCE_LENGTH + 1),
    ],
)
def test_detect_source_applies_length_guard_at_boundary(
    source_length: int,
    expected_source: Optional[str],
    expected_discarded_length: Optional[int],
) -> None:
    _, fake_post = _completion_result("x" * source_length)

    article = {"title": "测试标题", "content": "正文内容"}
    with patch("src.adapters.llm_source.post_chat_completion", side_effect=fake_post):
        result = llm_source.detect_source(article, retries=1)

    assert result["llm_source"] == expected_source
    assert result["source_guard_discarded_length"] == expected_discarded_length
    assert result["source_guard_triggered_attempt"] == (
        1 if expected_discarded_length is not None else 0
    )


def test_detect_source_rejects_oversized_single_line_response() -> None:
    oversized = "这是模型未换行的分析过程。" * 30
    _, fake_post = _completion_result(oversized)

    article = {"title": "测试标题", "content": "正文内容"}
    with patch("src.adapters.llm_source.post_chat_completion", side_effect=fake_post):
        result = llm_source.detect_source(article, retries=1)

    assert result["llm_source"] is None
    assert result["source_guard_discarded_length"] == len(oversized)
    assert result["source_guard_triggered_attempt"] == 1


def test_detect_source_reports_successful_http_attempt_for_length_guard() -> None:
    oversized = "x" * (MAX_LLM_SOURCE_LENGTH + 1)
    _, fake_post = _completion_result(oversized, attempt=3)

    with patch("src.adapters.llm_source.post_chat_completion", side_effect=fake_post):
        result = llm_source.detect_source(
            {"title": "测试标题", "content": "正文内容"},
            retries=4,
        )

    assert result["source_guard_triggered_attempt"] == 3
