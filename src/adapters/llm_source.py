from __future__ import annotations

import time
from typing import Any, Dict, Optional

from src.adapters.llm_chat import (
    apply_reasoning_config,
    build_headers,
    post_chat_completion,
)
from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_LLM_SOURCE_LENGTH = 64


def build_source_payload(article: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the chat completion payload for extracting a news source."""

    title = article.get("title")
    content = article.get("content_markdown") or article.get("content") or ""
    if not content:
        raise ValueError("Article content is required for source detection")
    prompt_parts = [
        "请阅读以下新闻内容，并判断这篇文章的发布/署名媒体名称。",
        "优先依据文章标题附近、正文开头或结尾中的“来源：”“转载自”“发布机构”“作者/署名”等明确来源信息。",
        "正文中出现的“某媒体报道”“某媒体了解到”“据某媒体”等通常只是引用报道来源，不要优先当作整篇文章的发布媒体。",
        "如果多个媒体同时出现，选择最像页面署名或版权来源的媒体；如果无法确定，请回答“未知”。",
        "仅返回媒体名称本身，不要包含额外说明。",
    ]
    if title:
        prompt_parts.append(f"标题：{title}")
    prompt_parts.append("正文：")
    prompt_parts.append(str(content))
    message = "\n".join(prompt_parts)
    return {"messages": [{"role": "user", "content": message}]}


def _normalise_response(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned:
        return ""
    first_line = cleaned.splitlines()[0].strip()
    prefixes = ("来源：", "来源:", "原文来源：", "原文来源:", "发布机构：", "发布机构:")
    for prefix in prefixes:
        if first_line.startswith(prefix):
            first_line = first_line[len(prefix) :].strip()
            break
    if first_line.endswith("：") or first_line.endswith(":"):
        first_line = first_line[:-1].strip()
    return first_line


def detect_source(
    article: Dict[str, Any],
    *,
    retries: int = 4,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Call the configured LLM chat completions API to infer the article source."""

    started_at = time.monotonic()
    settings = get_settings()
    deadline = started_at + settings.llm_source_budget
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set LLM_API_KEY)")

    payload = build_source_payload(article)
    payload.update(
        {
            "model": settings.llm_source_model,
            "temperature": 0,
        }
    )
    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=settings.llm_source_reasoning_enabled,
    )

    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    headers = build_headers(
        api_key=api_key,
        referer=settings.llm_api_http_referer,
        title=settings.llm_api_title,
    )

    resolved_timeout = timeout or settings.llm_summary_timeout
    current_attempt = 0

    def record_attempt(attempt: int) -> None:
        nonlocal current_attempt
        current_attempt = attempt

    def extract_raw_text(data: dict[str, Any]) -> str:
        return (
            data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()

    data = post_chat_completion(
        url,
        payload=payload,
        headers=headers,
        timeout=resolved_timeout,
        budget=settings.llm_source_budget,
        retries=retries,
        retryable_statuses=_RETRYABLE_STATUS,
        operation="source_detection",
        model=settings.llm_source_model,
        deadline=deadline,
        attempt_callback=record_attempt,
    )
    raw_text = extract_raw_text(data)
    llm_source = _normalise_response(raw_text)
    discarded_length: Optional[int] = None
    guard_triggered_attempt = 0
    if len(llm_source) > MAX_LLM_SOURCE_LENGTH:
        discarded_length = len(llm_source)
        guard_triggered_attempt = current_attempt
        llm_source = None
    if llm_source == "未知":
        llm_source = None
    return {
        "llm_source": llm_source,
        "model": settings.llm_source_model,
        "raw": data,
        "source_guard_discarded_length": discarded_length,
        "source_guard_triggered_attempt": guard_triggered_attempt,
    }


__all__ = ["MAX_LLM_SOURCE_LENGTH", "build_source_payload", "detect_source"]
