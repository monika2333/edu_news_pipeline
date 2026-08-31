from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional, Tuple

from src.adapters.llm_chat import (
    apply_reasoning_config,
    build_headers,
    post_chat_completion,
)
from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_POSITIVE_TOKENS = {"positive", "pos", "good", "favorable", "favourable"}
_NEGATIVE_TOKENS = {"negative", "neg", "bad", "unfavorable", "unfavourable"}




def _build_prompt(text: str) -> Dict[str, str]:
    base = text.strip()
    if not base:
        raise ValueError("Sentiment classification requires non-empty text")
    instruction = (
        "你是一名舆情分析员，需要判断以下教育相关新闻的整体倾向，请只输出 JSON。\n"
        "允许的标签：'positive' 或 'negative'。\n"
        "判定规则：\n"
        "- 如果报道聚焦于负面事件、风险、事故、腐败或违规被查处、批评问责、群体质疑等舆情，判为 'negative'；\n"
        "- 除上述情况外（包括正面或中性内容），一律判为 'positive'。\n"
        "输出格式（仅一行）：\n"
        "{\"label\":\"positive or negative\",\"confidence\": 小数0到1}\n"
        "不要输出中性标签或任何附加文字。"
    )
    return {
        "role": "user",
        "content": f"{instruction}\n\nContent:\n{base}",
    }
def _parse_response(raw_text: str) -> Tuple[str, float]:
    text = raw_text.strip()
    if not text:
        raise ValueError("Empty sentiment response")
    try:
        data = json.loads(text)
        label = str(data.get("label") or "").strip().lower()
        confidence = float(data.get("confidence")) if data.get("confidence") is not None else 0.5
    except Exception:
        lower = text.lower()
        if "positive" in lower or any(token in lower for token in _POSITIVE_TOKENS):
            label = "positive"
        elif "negative" in lower or any(token in lower for token in _NEGATIVE_TOKENS):
            label = "negative"
        else:
            raise ValueError(f"Unable to parse sentiment label from: {text[:160]}")
        confidence = 0.5
    label = "positive" if label in _POSITIVE_TOKENS else ("negative" if label in _NEGATIVE_TOKENS else label)
    if label not in {"positive", "negative"}:
        label = "positive"
    confidence = max(0.0, min(1.0, confidence))
    return label, confidence


def classify_sentiment(content: str, *, retries: int = 4, timeout: Optional[int] = None) -> Dict[str, object]:
    started_at = time.monotonic()
    settings = get_settings()
    deadline = started_at + settings.llm_sentiment_budget
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set LLM_API_KEY)")

    message = _build_prompt(content)
    payload = {
        "model": settings.llm_sentiment_model,
        "messages": [message],
        "temperature": 0.0,
    }
    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=settings.llm_sentiment_reasoning_enabled,
    )
    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    headers = build_headers(
        api_key=api_key,
        referer=settings.llm_api_http_referer,
        title=settings.llm_api_title,
    )

    resolved_timeout = timeout or settings.llm_summary_timeout

    def validate_response(data: dict[str, Any]) -> None:
        raw_text = (data["choices"][0]["message"]["content"] or "").strip()
        _parse_response(raw_text)

    data = post_chat_completion(
        url,
        payload=payload,
        headers=headers,
        timeout=resolved_timeout,
        budget=settings.llm_sentiment_budget,
        retries=retries,
        retryable_statuses=_RETRYABLE_STATUS,
        operation="sentiment",
        model=settings.llm_sentiment_model,
        deadline=deadline,
        response_validator=validate_response,
    )
    raw_text = (data["choices"][0]["message"]["content"] or "").strip()
    label, confidence = _parse_response(raw_text)
    return {
        "label": label,
        "confidence": confidence,
        "raw": data,
    }


__all__ = ["classify_sentiment"]

