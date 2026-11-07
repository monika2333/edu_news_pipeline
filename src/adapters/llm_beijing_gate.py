from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from src.adapters.llm_chat import extract_message_text, post_chat_completion
from src.adapters.llm_endpoint import ResolvedLLMEndpoint, resolve_llm_endpoint
from src.business_config import API_STYLE_OPENROUTER, get_llm_step_config
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
JSON_OBJECT_RESPONSE_FORMAT: dict[str, Any] = {"type": "json_object"}
# json_schema 是 OpenAI 的结构化输出扩展；原生 provider（DeepSeek 等）收到
# type=json_schema 会直接 400 "This response_format type is unavailable now"，
# 只支持 JSON mode。字段契约已写在提示词里，解析失败仍会重试，因此这里按
# 接入点能力降级，而不是让整个 geo_classify 步骤不可用。
JSON_SCHEMA_CAPABLE_API_STYLES = frozenset({API_STYLE_OPENROUTER})

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


def build_prompt(
    candidate: BeijingGateCandidate,
    *,
    template: Optional[str] = None,
) -> str:
    resolved_template = template if template is not None else _load_prompt_template()
    title = candidate.title or "（无标题）"
    summary = (candidate.summary or "").strip() or "（无摘要）"
    content = _truncate(candidate.content)
    return (
        f"{resolved_template}\n\n"
        "【待判定新闻】\n"
        f"标题：{title}\n"
        f"摘要：{summary}\n"
        f"正文摘录：{content}\n"
    )


def _resolve_timeout(settings) -> int:
    value = getattr(settings, "llm_beijing_gate_timeout", None)
    if isinstance(value, int) and value > 0:
        return value
    return settings.llm_external_filter_timeout


def _post_chat_completion(
    payload: Mapping[str, Any],
    *,
    endpoint: ResolvedLLMEndpoint,
    retries: int,
    timeout: int,
    deadline: Optional[float] = None,
) -> BeijingGateResponse:
    settings = get_settings()
    def validate_response(data: dict[str, Any]) -> None:
        choice = data.get("choices", [{}])[0]
        if not extract_message_text(choice):
            raise RuntimeError("Empty response from Beijing gate model")

    data = post_chat_completion(
        endpoint.chat_url,
        payload=payload,
        headers=endpoint.headers(),
        timeout=timeout,
        budget=settings.llm_beijing_gate_budget,
        retries=retries,
        retryable_statuses=RETRYABLE_STATUS,
        operation="beijing_gate",
        model=str(payload["model"]),
        deadline=deadline,
        response_validator=validate_response,
        endpoint_label=endpoint.label,
    )
    choice = data.get("choices", [{}])[0]
    message = extract_message_text(choice)
    provider = data.get("provider")
    model = data.get("model")
    return BeijingGateResponse(
        raw_text=message,
        provider=str(provider).strip() if provider else None,
        model=str(model).strip() if model else None,
    )


def build_response_format(endpoint: ResolvedLLMEndpoint) -> dict[str, Any]:
    """Return the structured-output mode the endpoint accepts.

    OpenRouter/OpenAI-compatible providers enforce the strict json_schema
    contract. Native providers implementing only OpenAI's JSON mode (DeepSeek
    is the current example) reject ``json_schema`` with HTTP 400, so they get
    ``json_object`` and rely on the prompt plus ``_parse_decision`` validation.
    """

    if endpoint.api_style in JSON_SCHEMA_CAPABLE_API_STYLES:
        return BEIJING_GATE_RESPONSE_FORMAT
    return JSON_OBJECT_RESPONSE_FORMAT


def request_extras(endpoint: ResolvedLLMEndpoint) -> dict[str, Any]:
    """京内判定请求里 model/messages/max_tokens 之外的固定参数。

    真实请求与保存接入点时的模型实测（settings_service._probe_model）
    都用本函数的返回值拼请求体——额外参数只有这一份定义：
    response_format 经 build_response_format 随接入点能力降级，两处永远
    一致，不会出现「实测通过、跑到京内判定时报 400」。"""

    return {
        "temperature": 0,
        "response_format": build_response_format(endpoint),
    }


def call_beijing_gate(
    candidate: BeijingGateCandidate,
    *,
    retries: int = 3,
    prompt_template: Optional[str] = None,
) -> BeijingGateDecision:
    started_at = time.monotonic()
    settings = get_settings()
    step_config = get_llm_step_config("beijing_gate")
    model = step_config.model
    deadline = started_at + settings.llm_beijing_gate_budget
    endpoint = resolve_llm_endpoint("beijing_gate")
    prompt = build_prompt(candidate, template=prompt_template)
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        **request_extras(endpoint),
    }
    endpoint.finalize_payload(
        payload,
        settings=settings,
        reasoning_enabled=step_config.reasoning,
    )
    timeout = _resolve_timeout(settings)
    semantic_attempts = max(1, retries)
    response = _post_chat_completion(
        payload,
        endpoint=endpoint,
        retries=semantic_attempts,
        timeout=timeout,
        deadline=deadline,
    )
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
            response = _post_chat_completion(
                payload,
                endpoint=endpoint,
                retries=1,
                timeout=timeout,
                deadline=deadline,
            )
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
    "build_response_format",
    "call_beijing_gate",
    "request_extras",
]
