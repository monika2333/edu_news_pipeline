from __future__ import annotations

import time
from typing import Any, Dict, Optional

from src.adapters.llm_chat import post_chat_completion
from src.adapters.llm_endpoint import resolve_llm_endpoint
from src.business_config import get_llm_step_config
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

    started_at = time.monotonic()
    settings = get_settings()
    step_config = get_llm_step_config("summary")
    model = step_config.model
    deadline = started_at + settings.llm_summary_budget
    endpoint = resolve_llm_endpoint("summary")

    payload = build_summary_payload(article)
    payload.update(
        {
            "model": model,
            "temperature": 0.2,
        }
    )
    endpoint.finalize_payload(
        payload,
        settings=settings,
        reasoning_enabled=step_config.reasoning,
    )

    # Resolve timeout from settings if not explicitly provided
    resolved_timeout = timeout or settings.llm_summary_timeout
    data = post_chat_completion(
        endpoint.chat_url,
        payload=payload,
        headers=endpoint.headers(),
        timeout=resolved_timeout,
        budget=settings.llm_summary_budget,
        retries=retries,
        retryable_statuses=_RETRYABLE_STATUS,
        operation="summarize",
        model=model,
        deadline=deadline,
        endpoint_label=endpoint.label,
    )
    summary = (data["choices"][0]["message"]["content"] or "").strip()
    return {
        "summary": summary,
        "model": model,
        "raw": data,
    }


__all__ = ["build_summary_payload", "summarise"]
