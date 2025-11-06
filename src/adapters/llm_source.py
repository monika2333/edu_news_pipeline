from __future__ import annotations

import time
from typing import Any, Dict, Optional

import requests

from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def build_source_payload(article: Dict[str, Any]) -> Dict[str, Any]:
    """Construct the chat completion payload for extracting a news source."""

    title = article.get("title")
    content = article.get("content_markdown") or article.get("content") or ""
    if not content:
        raise ValueError("Article content is required for source detection")
    prompt_parts = [
        "请阅读以下文章内容，并判断原始发布的媒体名称。",
        "如果无法确定，请回答“未知”。",
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
    timeout: int = 60,
) -> Dict[str, Any]:
    """Call SiliconFlow chat completions API to infer the article source."""

    settings = get_settings()
    api_key = settings.siliconflow_api_key
    if not api_key:
        raise RuntimeError("Missing SILICONFLOW_API_KEY environment variable")

    payload = build_source_payload(article)
    payload.update(
        {
            "model": settings.source_model_name,
            "temperature": 0,
        }
    )
    if settings.siliconflow_enable_thinking:
        payload["enable_thinking"] = True

    url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                raw_text = (data.get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
                llm_source = _normalise_response(raw_text)
                if llm_source == "未知":
                    llm_source = ""
                return {
                    "llm_source": llm_source,
                    "model": settings.source_model_name,
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
