from __future__ import annotations

import re
import time
from typing import Optional

import requests

from src.adapters.llm_chat import apply_reasoning_config, build_headers, extract_message_text
from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_SCORE_PATTERN = re.compile(r"(\d{1,3})")


def _build_prompt(text: str) -> str:
    return (
        "请判断下面的内容和教育的相关程度，输出{0-100}，其中0表示完全不相关，100表示完全相关，直接输出数字即可：\n"
        f"{text}"
    )


def call_relevance_api(text: str, *, retries: int = 4, timeout: Optional[int] = None) -> str:
    settings = get_settings()
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set OPENROUTER_API_KEY or LLM_API_KEY)")

    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.score_model_name,
        "messages": [{"role": "user", "content": _build_prompt(text)}],
        "temperature": 0.0,
    }
    apply_reasoning_config(
        payload,
        settings=settings,
        base_url=settings.llm_base_url,
        enabled=settings.llm_enable_thinking,
    )

    headers = build_headers(
        api_key=api_key,
        referer=settings.llm_http_referer,
        title=settings.llm_title,
    )

    backoff = 1.0
    last_error: Optional[Exception] = None
    # Resolve timeout from settings if not explicitly provided
    resolved_timeout = timeout or settings.llm_timeout_score

    for _ in range(max(1, retries)):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=resolved_timeout)
            if resp.status_code == 200:
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                return extract_message_text(choice)
            if resp.status_code in _RETRYABLE_STATUS:
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            last_error = RuntimeError(f"API {resp.status_code}: {resp.text[:160]}")
        except Exception as exc:
            last_error = exc
        time.sleep(backoff)
    raise last_error or RuntimeError("Relevance scoring call failed")


def parse_score(value: str) -> Optional[int]:
    if not value:
        return None
    match = _SCORE_PATTERN.search(value)
    if not match:
        return None
    score = int(match.group(1))
    if score < 0:
        score = 0
    if score > 100:
        score = 100
    return score


def score_text(text: str) -> Optional[int]:
    raw = call_relevance_api(text)
    return parse_score(raw)


__all__ = ["call_relevance_api", "parse_score", "score_text"]
