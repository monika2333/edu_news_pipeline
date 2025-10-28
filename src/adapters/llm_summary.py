from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

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
    message = "请概括下面的内容为一段话：\n" + "\n".join(prompt_parts)
    return {"messages": [{"role": "user", "content": message}]}


def summarise(
    article: Dict[str, Any],
    *,
    retries: int = 4,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Call SiliconFlow chat completions API to summarise an article."""

    settings = get_settings()
    api_key = settings.siliconflow_api_key
    if not api_key:
        raise RuntimeError("Missing SILICONFLOW_API_KEY environment variable")

    payload = build_summary_payload(article)
    payload.update(
        {
            "model": settings.summarize_model_name,
            "temperature": 0.2,
        }
    )
    if settings.siliconflow_enable_thinking:
        payload["enable_thinking"] = True

    url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    backoff = 1.0
    last_error: Optional[Exception] = None
    # Resolve timeout from settings if not explicitly provided
    resolved_timeout = timeout or settings.siliconflow_timeout_summary

    for _ in range(max(1, retries)):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=resolved_timeout)
            if response.status_code == 200:
                data = response.json()
                summary = (data["choices"][0]["message"]["content"] or "").strip()
                return {
                    "summary": summary,
                    "model": settings.summarize_model_name,
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
