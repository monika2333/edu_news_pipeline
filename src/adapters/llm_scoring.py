from __future__ import annotations

import re
import time
from typing import Optional

from src.adapters.llm_chat import (
    apply_reasoning_config,
    build_headers,
    extract_message_text,
    post_chat_completion,
)
from src.config import get_settings

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_SCORE_PATTERN = re.compile(r"(\d{1,3})")


def _build_prompt(text: str) -> str:
    return (
        "请判断下面新闻内容与教育的相关程度，并输出 0-100 的整数分数。\n"
        "这里评估的是教育相关性，不是新闻的重要性、政策级别或报送价值。\n\n"
        "评分参考：\n"
        "- 90-100：教育政策、教学育人、学校治理、招生考试、教师学生、校园安全等是新闻核心。\n"
        "- 75-89：学校、高校、教育主管部门或其师生团队是事件的主要行动、研发或完成主体；"
        "- 60-74：教育主体承担实质角色或教育内容占有明显篇幅，但新闻同时重点讨论其他领域。\n"
        "- 30-59：教育主体仅是参与方、活动场地或背景信息，与教育的实质联系较弱。\n"
        "- 0-29：没有具体教育主体或教育内容，或学校名称只出现在来源署名、地点和无关背景中。\n\n"
        "只输出一个整数，不要解释。\n"
        "新闻内容：\n"
        f"{text}"
    )


def call_relevance_api(text: str, *, retries: int = 4, timeout: Optional[int] = None) -> str:
    started_at = time.monotonic()
    settings = get_settings()
    deadline = started_at + settings.llm_scoring_budget
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set LLM_API_KEY)")

    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_scoring_model,
        "messages": [{"role": "user", "content": _build_prompt(text)}],
        "temperature": 0.0,
    }
    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=settings.llm_reasoning_enabled,
    )

    headers = build_headers(
        api_key=api_key,
        referer=settings.llm_api_http_referer,
        title=settings.llm_api_title,
    )

    # Resolve timeout from settings if not explicitly provided
    resolved_timeout = timeout or settings.llm_scoring_timeout
    data = post_chat_completion(
        url,
        payload=payload,
        headers=headers,
        timeout=resolved_timeout,
        budget=settings.llm_scoring_budget,
        retries=retries,
        retryable_statuses=_RETRYABLE_STATUS,
        operation="score",
        model=settings.llm_scoring_model,
        deadline=deadline,
        advance_backoff_on_exception=False,
    )
    choice = data.get("choices", [{}])[0]
    return extract_message_text(choice)


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
