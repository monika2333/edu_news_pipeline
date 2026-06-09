from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from src.adapters.llm_chat import apply_reasoning_config, build_headers
from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


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

    settings = get_settings()
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
        enabled=settings.llm_summary_reasoning_enabled,
    )

    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    headers = build_headers(
        api_key=api_key,
        referer=settings.llm_api_http_referer,
        title=settings.llm_api_title,
    )

    backoff = 1.0
    last_error: Optional[Exception] = None
    resolved_timeout = timeout or settings.llm_summary_timeout
    for _ in range(max(1, retries)):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=resolved_timeout)
            if response.status_code == 200:
                data = response.json()
                raw_text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                llm_source = _normalise_response(raw_text)
                if llm_source == "未知":
                    llm_source = None
                return {
                    "llm_source": llm_source,
                    "model": settings.llm_source_model,
                    "raw": data,
                }
            if response.status_code in _RETRYABLE_STATUS:
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            last_error = RuntimeError(f"API {response.status_code}: {response.text[:160]}")
        except Exception as exc:
            last_error = exc
        time.sleep(backoff)
        backoff = min(backoff * 2, 8)
    raise last_error or RuntimeError("Source detection call failed")


__all__ = ["build_source_payload", "detect_source"]
