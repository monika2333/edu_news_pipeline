from __future__ import annotations

import json
import math
import re
import time
from typing import Dict, Optional, Tuple

import requests

from src.config import Settings, get_settings

POSITIVE_KEYWORDS = {
    "表彰",
    "表扬",
    "先进",
    "优秀",
    "优质",
    "成果",
    "成效",
    "提升",
    "改善",
    "喜讯",
    "突破",
    "创新",
    "优化",
    "亮点",
    "荣誉",
    "嘉奖",
    "颁奖",
    "签约",
    "成功",
    "建设",
    "落地",
    "支持",
    "推动",
    "保障",
    "惠民",
    "利好",
}

NEGATIVE_KEYWORDS = {
    "事故",
    "意外",
    "违规",
    "违法",
    "问题",
    "投诉",
    "纠纷",
    "整改",
    "查处",
    "处罚",
    "批评",
    "通报",
    "曝光",
    "警示",
    "停课",
    "退学",
    "欺凌",
    "压减",
    "预警",
    "隐患",
    "拖欠",
    "风险",
    "死亡",
    "受伤",
    "感染",
    "谴责",
    "问责",
    "调查",
}

JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _clip(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _small_model_predict(text: str) -> Tuple[str, float]:
    lowered = text.lower()
    pos_hits = sum(1 for keyword in POSITIVE_KEYWORDS if keyword in text or keyword.lower() in lowered)
    neg_hits = sum(1 for keyword in NEGATIVE_KEYWORDS if keyword in text or keyword.lower() in lowered)
    total_hits = pos_hits + neg_hits
    if total_hits == 0:
        return "positive", 0.0
    if pos_hits >= neg_hits:
        label = "positive"
    else:
        label = "negative"
    confidence = abs(pos_hits - neg_hits) / total_hits
    # Penalise very short texts
    if len(text.strip()) < 200 and confidence > 0:
        confidence *= 0.8
    return label, _clip(confidence)


def _build_llm_payload(content: str, *, settings: Settings) -> Dict[str, object]:
    prompt = (
        "请作为教育行业舆情分析人员，判断以下新闻报道的整体情感倾向（正面或负面）。\n"
        "请从教育主管部门的视角，综合判断报道对教育系统的影响。\n"
        "请仅输出一个 JSON，对应格式为：\n"
        '{"label": "positive 或 negative", "confidence": 0.0 到 1.0}\n'
        "其中 confidence 代表你对判断的把握程度。\n"
        "正文如下：\n"
        f"{content.strip()}\n"
    )
    return {
        "model": settings.sentiment_model_name,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
    }


def _extract_json_block(text: str) -> Optional[str]:
    match = JSON_BLOCK_RE.search(text)
    if match:
        return match.group(0)
    return None


def _llm_predict(content: str, *, settings: Settings, retries: int = 3, timeout: int = 40) -> Tuple[str, float]:
    api_key = settings.siliconflow_api_key
    if not api_key:
        raise RuntimeError("缺少 SILICONFLOW_API_KEY，无法调用情感模型。")
    payload = _build_llm_payload(content, settings=settings)
    url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                message = data["choices"][0]["message"]["content"]
                block = _extract_json_block(message) or message.strip()
                parsed = json.loads(block)
                label = str(parsed.get("label", "")).strip().lower()
                if label not in {"positive", "negative"}:
                    label = "positive"
                confidence = _clip(float(parsed.get("confidence", 0.7)))
                return label, confidence
            if response.status_code in {429, 500, 502, 503, 504}:
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            last_error = RuntimeError(f"Sentiment API {response.status_code}: {response.text[:160]}")
        except Exception as exc:
            last_error = exc
        time.sleep(backoff)
        backoff = min(backoff * 2, 8)
    if last_error:
        raise last_error
    raise RuntimeError("Sentiment classification failed without response")


def classify_sentiment(
    content: str,
    *,
    settings: Optional[Settings] = None,
    threshold: Optional[float] = None,
) -> Tuple[str, float, str]:
    settings = settings or get_settings()
    content = (content or "").strip()
    if not content:
        return "negative", 0.0, "empty"

    label, confidence = _small_model_predict(content)
    cutoff = threshold if threshold is not None else settings.sentiment_confidence_threshold
    if confidence >= cutoff:
        return label, confidence, "small"

    try:
        llm_label, llm_confidence = _llm_predict(content[:2000], settings=settings)
        return llm_label, llm_confidence, "large"
    except Exception:
        # Fallback: trust small model result even if低置信度
        return label, confidence, "small_fallback"


__all__ = ["classify_sentiment"]
