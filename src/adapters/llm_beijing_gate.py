from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import requests

from src.config import get_settings
from src.domain import BeijingGateCandidate

PROMPT_PATH = Path(__file__).resolve().parents[2] / "docs" / "beijing_gate_prompt.md"
PROMPT_TAG_PATTERN = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

_PROMPT_CACHE: Optional[str] = None


@dataclass(frozen=True)
class BeijingGateDecision:
    is_beijing_related: Optional[bool]
    reason: Optional[str]
    raw_text: str


def _load_prompt_template() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    if not PROMPT_PATH.exists():
        _PROMPT_CACHE = ""
        return _PROMPT_CACHE
    content = PROMPT_PATH.read_text(encoding="utf-8")
    match = PROMPT_TAG_PATTERN.search(content)
    template = match.group(1).strip() if match else content.strip()
    _PROMPT_CACHE = template
    return _PROMPT_CACHE


def _truncate(text: str, limit: int = 1500) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}…（内容截断）"


def build_prompt(candidate: BeijingGateCandidate) -> str:
    template = _load_prompt_template()
    title = candidate.title or "（无标题）"
    summary = (candidate.summary or "").strip() or "（无摘要）"
    content = _truncate(candidate.content)
    return (
        f"{template}\n\n"
        "【待判定新闻】\n"
        f"标题：{title}\n"
        f"摘要：{summary}\n"
        f"正文摘录：{content}\n"
    )


def _resolve_model_name(settings) -> str:
    return getattr(settings, "beijing_gate_model_name", None) or settings.score_model_name


def _resolve_timeout(settings) -> int:
    value = getattr(settings, "llm_timeout_beijing_gate", None)
    if isinstance(value, int) and value > 0:
        return value
    return settings.llm_timeout_external_filter


def _post_chat_completion(payload: Mapping[str, Any], retries: int, timeout: int) -> str:
    settings = get_settings()
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set OPENROUTER_API_KEY or LLM_API_KEY)")
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if settings.llm_http_referer:
        headers["HTTP-Referer"] = settings.llm_http_referer
    if settings.llm_title:
        headers["X-Title"] = settings.llm_title
    backoff = 1.0
    last_error: Optional[Exception] = None
    for _ in range(max(1, retries)):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                choice = data.get("choices", [{}])[0]
                message = (choice.get("message", {}) or {}).get("content")
                if message:
                    return str(message).strip()
                reasoning = choice.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning.strip():
                    return reasoning.strip()
                if isinstance(reasoning, list):
                    flattened = " ".join(str(part).strip() for part in reasoning if part)
                    if flattened.strip():
                        return flattened.strip()
                raise RuntimeError("Empty response from Beijing gate model")
            if response.status_code in RETRYABLE_STATUS:
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            last_error = RuntimeError(f"API {response.status_code}: {response.text[:160]}")
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        time.sleep(backoff)
    raise last_error or RuntimeError("Beijing gate model call failed")


def call_beijing_gate(candidate: BeijingGateCandidate, *, retries: int = 3) -> BeijingGateDecision:
    settings = get_settings()
    prompt = build_prompt(candidate)
    payload: dict[str, Any] = {
        "model": _resolve_model_name(settings),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if getattr(settings, "llm_enable_thinking", False):
        payload["enable_thinking"] = True
    timeout = _resolve_timeout(settings)
    raw_output = _post_chat_completion(payload, retries=retries, timeout=timeout)
    decision = _parse_decision(raw_output)
    return BeijingGateDecision(
        is_beijing_related=decision["is_beijing_related"],
        reason=decision["reason"],
        raw_text=raw_output,
    )


def _parse_decision(raw_output: str) -> dict[str, Optional[Any]]:
    text = (raw_output or "").strip()
    if not text:
        return {"is_beijing_related": None, "reason": None}

    parsed: Optional[dict[str, Any]] = None
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            parsed = loaded
    except json.JSONDecodeError:
        # Attempt to locate JSON snippet within the text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                loaded = json.loads(match.group(0))
                if isinstance(loaded, dict):
                    parsed = loaded
            except json.JSONDecodeError:
                parsed = None

    if parsed is not None:
        state = parsed.get("is_beijing_related")
        reason = parsed.get("reason")
        return {
            "is_beijing_related": _coerce_bool(state),
            "reason": str(reason).strip() if reason is not None else None,
        }

    # Fallback heuristic
    lowered = text.lower()
    if "true" in lowered or "是" in raw_output:
        return {"is_beijing_related": True, "reason": raw_output}
    if "false" in lowered or "否" in raw_output:
        return {"is_beijing_related": False, "reason": raw_output}
    return {"is_beijing_related": None, "reason": raw_output}


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "y", "是"}:
            return True
        if lowered in {"false", "no", "n", "否"}:
            return False
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return None


__all__ = [
    "BeijingGateDecision",
    "build_prompt",
    "call_beijing_gate",
]
