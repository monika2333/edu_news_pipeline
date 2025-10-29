from __future__ import annotations

import json
import time
from typing import Dict, Optional, Tuple

import requests

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


def classify_sentiment(content: str, *, retries: int = 4, timeout: int = 45) -> Dict[str, object]:
    settings = get_settings()
    api_key = settings.siliconflow_api_key
    if not api_key:
        raise RuntimeError("Missing SILICONFLOW_API_KEY environment variable")

    message = _build_prompt(content)
    payload = {
        "model": settings.sentiment_model_name,
        "messages": [message],
        "temperature": 0.0,
    }
    url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                content = (data["choices"][0]["message"]["content"] or "").strip()
                label, confidence = _parse_response(content)
                return {
                    "label": label,
                    "confidence": confidence,
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
    raise last_error or RuntimeError("Sentiment classification failed")


__all__ = ["classify_sentiment"]

