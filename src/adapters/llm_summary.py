from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from src.adapters.llm_chat import apply_reasoning_config, build_headers
from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def build_summary_payload(article: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the chat completion payload for the summarisation request."""

    title = article.get("title")
    content = article.get("content") or ""
    if not content:
        raise ValueError("Article content is required for summarisation")
    prompt_parts = []
    if title:
        prompt_parts.append(f"标题：{title}")
    prompt_parts.append("正文：")
    prompt_parts.append(str(content))
    instruction = (
        "请将下面的新闻概括为一段约200字的摘要。"
        "摘要应覆盖核心事实，包括时间、主体、事件、地点、结果或影响；"
        "不要分条，不要添加原文没有的信息。"
    )
    message = f"{instruction}\n" + "\n".join(prompt_parts)
    return {"messages": [{"role": "user", "content": message}]}


def summarise(
    article: Dict[str, Any],
    *,
    retries: int = 4,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Call the configured LLM chat completions API to summarise an article."""

    settings = get_settings()
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set LLM_API_KEY)")

    payload = build_summary_payload(article)
    payload.update(
        {
            "model": settings.llm_summary_model,
            "temperature": 0.2,
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
    # Resolve timeout from settings if not explicitly provided
    resolved_timeout = timeout or settings.llm_summary_timeout

    for _ in range(max(1, retries)):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=resolved_timeout)
            if response.status_code == 200:
                data = response.json()
                summary = (data["choices"][0]["message"]["content"] or "").strip()
                return {
                    "summary": summary,
                    "model": settings.llm_summary_model,
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
    raise last_error or RuntimeError("Summarisation call failed")


__all__ = ["build_summary_payload", "summarise"]
