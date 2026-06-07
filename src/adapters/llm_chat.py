from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional

from src.config import Settings


def _is_openrouter_base_url(base_url: str) -> bool:
    return "openrouter.ai" in (base_url or "").lower()


def build_headers(
    *,
    api_key: str,
    referer: Optional[str],
    title: Optional[str],
) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if referer:
        headers["HTTP-Referer"] = referer
    if title:
        headers["X-Title"] = title
    return headers


def apply_reasoning_config(
    payload: MutableMapping[str, Any],
    *,
    settings: Settings,
    base_url: str,
    enabled: bool,
) -> None:
    if not enabled:
        return

    effort = (settings.llm_reasoning_effort or "").strip().lower()
    max_tokens = settings.llm_reasoning_max_tokens
    exclude = settings.llm_reasoning_exclude

    if _is_openrouter_base_url(base_url):
        reasoning: dict[str, Any] = {"enabled": True}
        if effort:
            reasoning["effort"] = effort
        if max_tokens is not None and max_tokens > 0:
            reasoning["max_tokens"] = max_tokens
        if exclude:
            reasoning["exclude"] = True
        payload["reasoning"] = reasoning
        return

    payload["enable_thinking"] = True


def extract_message_text(choice: Mapping[str, Any]) -> str:
    message = choice.get("message")
    if isinstance(message, Mapping):
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        reasoning = message.get("reasoning")
        if isinstance(reasoning, str) and reasoning.strip():
            return reasoning.strip()

    reasoning_content = choice.get("reasoning_content")
    if isinstance(reasoning_content, str) and reasoning_content.strip():
        return reasoning_content.strip()
    if isinstance(reasoning_content, list):
        flattened = " ".join(str(part).strip() for part in reasoning_content if part)
        if flattened.strip():
            return flattened.strip()

    return ""


__all__ = [
    "apply_reasoning_config",
    "build_headers",
    "extract_message_text",
]
