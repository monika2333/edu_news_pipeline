from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import requests

from src.adapters.llm_chat import (
    LLMQuotaError,
    apply_reasoning_config,
    build_headers,
    extract_message_text,
    raise_for_llm_quota_error,
)
from src.config import get_settings
from src.domain import BeijingGateCandidate

PROMPT_TAG_PATTERN = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
BEIJING_GATE_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "beijing_gate_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "is_beijing_related": {"type": "boolean"},
                "reason": {"type": "string"},
            },
            "required": ["is_beijing_related", "reason"],
            "additionalProperties": False,
        },
    },
}

_PROMPT_CACHE: Optional[str] = None


@dataclass(frozen=True)
class BeijingGateResponse:
    raw_text: str
    provider: Optional[str]
    model: Optional[str]


@dataclass(frozen=True)
class BeijingGateDecision:
    is_beijing_related: Optional[bool]
    reason: Optional[str]
    raw_text: str
    provider: Optional[str] = None
    model: Optional[str] = None
    attempts: int = 1


class BeijingGateIndeterminateError(RuntimeError):
    """Raised when repeated model responses violate the Beijing gate contract."""

    def __init__(self, response: BeijingGateResponse, *, attempts: int) -> None:
        super().__init__("Beijing gate returned indeterminate result")
        self.raw_text = response.raw_text
        self.provider = response.provider
        self.model = response.model
        self.attempts = attempts

    def diagnostic_payload(self) -> dict[str, Any]:
        """Return structured details suitable for persistence."""
        return {
            "model_output": self.raw_text[:4000],
            "provider": self.provider,
            "model": self.model,
            "semantic_attempts": self.attempts,
        }


def _load_prompt_template() -> str:
    global _PROMPT_CACHE
    if _PROMPT_CACHE is not None:
        return _PROMPT_CACHE
    prompt_path = get_settings().beijing_gate_prompt_path
    if not prompt_path.exists():
        _PROMPT_CACHE = ""
        return _PROMPT_CACHE
    content = prompt_path.read_text(encoding="utf-8")
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
    return settings.llm_beijing_gate_model


def _resolve_timeout(settings) -> int:
    value = getattr(settings, "llm_beijing_gate_timeout", None)
    if isinstance(value, int) and value > 0:
        return value
    return settings.llm_external_filter_timeout


def _post_chat_completion(
    payload: Mapping[str, Any],
    retries: int,
    timeout: int,
) -> BeijingGateResponse:
    settings = get_settings()
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set LLM_API_KEY)")
    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    headers = build_headers(
        api_key=api_key,
        referer=settings.llm_api_http_referer,
        title=settings.llm_api_title,
    )
    backoff = 1.0
    last_error: Optional[Exception] = None
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                choice = data.get("choices", [{}])[0]
                message = extract_message_text(choice)
                if message:
                    provider = data.get("provider")
                    model = data.get("model")
                    return BeijingGateResponse(
                        raw_text=message,
                        provider=str(provider).strip() if provider else None,
                        model=str(model).strip() if model else None,
                    )
                raise RuntimeError("Empty response from Beijing gate model")
            raise_for_llm_quota_error(
                status_code=response.status_code,
                response_text=response.text,
                operation="beijing_gate",
                model=_resolve_model_name(settings),
            )
            if response.status_code in RETRYABLE_STATUS:
                last_error = RuntimeError(
                    f"API {response.status_code}: {response.text[:160]}"
                )
                if attempt < attempts - 1:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 8)
                continue
            last_error = RuntimeError(f"API {response.status_code}: {response.text[:160]}")
        except LLMQuotaError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
        if attempt < attempts - 1:
            time.sleep(backoff)
            backoff = min(backoff * 2, 8)
    raise last_error or RuntimeError("Beijing gate model call failed")


def call_beijing_gate(candidate: BeijingGateCandidate, *, retries: int = 3) -> BeijingGateDecision:
    settings = get_settings()
    prompt = build_prompt(candidate)
    payload: dict[str, Any] = {
        "model": _resolve_model_name(settings),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "response_format": BEIJING_GATE_RESPONSE_FORMAT,
    }
    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=settings.llm_reasoning_enabled,
    )
    timeout = _resolve_timeout(settings)
    semantic_attempts = max(1, retries)
    response = _post_chat_completion(payload, retries=semantic_attempts, timeout=timeout)
    for attempt in range(1, semantic_attempts + 1):
        decision = _parse_decision(response.raw_text)
        if decision["is_beijing_related"] is not None:
            return BeijingGateDecision(
                is_beijing_related=decision["is_beijing_related"],
                reason=decision["reason"],
                raw_text=response.raw_text,
                provider=response.provider,
                model=response.model,
                attempts=attempt,
            )
        if attempt < semantic_attempts:
            response = _post_chat_completion(payload, retries=1, timeout=timeout)
    raise BeijingGateIndeterminateError(response, attempts=semantic_attempts)


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
    "BeijingGateIndeterminateError",
    "build_prompt",
    "call_beijing_gate",
]
