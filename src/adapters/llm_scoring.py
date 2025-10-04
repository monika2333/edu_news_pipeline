from __future__ import annotations

import re
import time
from typing import Optional

import requests

from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_SCORE_PATTERN = re.compile(r"(\d{1,3})")


def _build_prompt(text: str) -> str:
    return (
        "请判断下面的内容和教育的相关程度，输出{0-100}，其中0表示完全不相关，100表示完全相关，直接输出数字即可：\n"
        f"{text}"
    )


def call_relevance_api(text: str, *, retries: int = 4, timeout: int = 30) -> str:
    settings = get_settings()
    api_key = settings.siliconflow_api_key
    if not api_key:
        raise RuntimeError("Missing SILICONFLOW_API_KEY environment variable")

    url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.score_model_name,
        "messages": [{"role": "user", "content": _build_prompt(text)}],
        "temperature": 0.0,
    }
    if settings.siliconflow_enable_thinking:
        payload["enable_thinking"] = True

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                choice = data.get("choices", [{}])[0]
                message_content = (choice.get("message", {}).get("content") or "").strip()
                if not message_content:
                    reasoning = choice.get("reasoning_content")
                    if isinstance(reasoning, str):
                        message_content = reasoning.strip()
                    elif isinstance(reasoning, list):
                        message_content = " ".join(str(part) for part in reasoning).strip()
                return message_content
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
