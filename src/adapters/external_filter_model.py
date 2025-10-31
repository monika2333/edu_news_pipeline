from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Dict, Optional

import requests

from src.adapters.llm_scoring import parse_score
from src.config import get_settings
from src.domain import ExternalFilterCandidate

_PROMPT_CACHE: Dict[str, str] = {}
_DEFAULT_PROMPT_PATHS = {
    "external": Path(__file__).resolve().parents[2] / "docs" / "external_filter_prompt.md",
    "internal": Path(__file__).resolve().parents[2] / "docs" / "internal_importance_prompt.md",
}
_PROMPT_TAG_PATTERN = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _get_prompt_path(category: str) -> Path:
    settings = get_settings()
    if category == "internal":
        return settings.internal_filter_prompt_path
    return _DEFAULT_PROMPT_PATHS.get(category, _DEFAULT_PROMPT_PATHS["external"])


def _load_prompt_template(category: str = "external") -> str:
    if category in _PROMPT_CACHE:
        return _PROMPT_CACHE[category]
    prompt_path = _get_prompt_path(category)
    if not prompt_path.exists():
        _PROMPT_CACHE[category] = ""
        return _PROMPT_CACHE[category]
    content = prompt_path.read_text(encoding="utf-8")
    match = _PROMPT_TAG_PATTERN.search(content)
    template = match.group(1).strip() if match else content.strip()
    _PROMPT_CACHE[category] = template
    return template


def _truncate(text: str, limit: int = 1500) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "……（内容截断）"


def build_prompt(candidate: ExternalFilterCandidate, *, category: str = "external") -> str:
    template = _load_prompt_template(category)
    title = candidate.title or "（无标题）"
    source = candidate.source or "（未知来源）"
    summary = (candidate.summary or "").strip() or "（无摘要）"
    content = _truncate(candidate.content or "")
    return (
        f"{template}\n\n"
        "【新闻内容】\n"
        f"标题：{title}\n"
        f"来源：{source}\n"
        f"摘要：{summary}\n"
        f"正文摘录：{content}\n"
    )


def call_external_filter_model(
    candidate: ExternalFilterCandidate,
    *,
    category: str = "external",
    retries: int = 3,
    timeout: Optional[int] = None,
) -> str:
    settings = get_settings()
    api_key = settings.siliconflow_api_key
    if not api_key:
        raise RuntimeError("Missing SILICONFLOW_API_KEY environment variable")
    url = f"{settings.siliconflow_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.external_filter_model_name,
        "messages": [{"role": "user", "content": build_prompt(candidate, category=category)}],
        "temperature": 0.0,
    }
    if settings.siliconflow_enable_thinking:
        payload["enable_thinking"] = True
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    backoff = 1.0
    last_error: Optional[Exception] = None
    # Resolve timeout from settings if not explicitly provided
    resolved_timeout = timeout or settings.siliconflow_timeout_external_filter

    for _ in range(max(1, retries)):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=resolved_timeout)
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
                if not message_content:
                    raise RuntimeError("Empty response from external filter model")
                return message_content
            if resp.status_code in _RETRYABLE_STATUS:
                time.sleep(backoff)
                backoff = min(backoff * 2, 8)
                continue
            last_error = RuntimeError(f"API {resp.status_code}: {resp.text[:160]}")
        except Exception as exc:
            last_error = exc
        time.sleep(backoff)
    raise last_error or RuntimeError("External filter model call failed")


def parse_external_filter_score(raw_output: str) -> Optional[int]:
    return parse_score(raw_output)


__all__ = ["build_prompt", "call_external_filter_model", "parse_external_filter_score"]
