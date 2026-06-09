from __future__ import annotations

import pytest

from unittest.mock import patch

from src.adapters import llm_source
from src.adapters.llm_source import build_source_payload


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
    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"choices": [{"message": {"content": "未知"}}]}

    article = {"title": "测试标题", "content": "正文内容"}
    with patch("src.adapters.llm_source.requests.post", return_value=_Response()):
        result = llm_source.detect_source(article, retries=1)

    assert result["llm_source"] is None


def test_detect_source_uses_source_reasoning_setting() -> None:
    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict[str, object]:
            return {"choices": [{"message": {"content": "测试媒体"}}]}

    article = {"title": "测试标题", "content": "正文内容"}
    with patch("src.adapters.llm_source.requests.post", return_value=_Response()) as post:
        result = llm_source.detect_source(article, retries=1)

    payload = post.call_args.kwargs["json"]
    assert result["llm_source"] == "测试媒体"
    assert payload["reasoning"]["enabled"] is True
