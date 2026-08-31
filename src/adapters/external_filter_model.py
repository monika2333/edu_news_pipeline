from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Optional

from src.adapters.llm_chat import (
    apply_reasoning_config,
    build_headers,
    extract_message_text,
    post_chat_completion,
)
from src.adapters.llm_scoring import parse_score
from src.config import get_settings
from src.domain import ExternalFilterCandidate

_PROMPT_CACHE: dict[str, str] = {}
_PROMPT_KEYS = frozenset(
    {
        "external_positive",
        "external_negative",
        "internal_positive",
        "internal_negative",
    }
)
_PROMPT_TAG_PATTERN = re.compile(r"<prompt>(.*?)</prompt>", re.DOTALL)
_PROMPT_VERSIONS_PATH = Path(__file__).resolve().parents[2] / "config" / "prompts" / "VERSIONS"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def prompt_key_for_category(category: Optional[str]) -> str:
    raw = (category or "external").strip().lower()
    if raw in _PROMPT_KEYS:
        return raw
    if raw.startswith("internal"):
        return "internal_negative" if "negative" in raw else "internal_positive"
    if raw.startswith("external"):
        return "external_negative" if "negative" in raw else "external_positive"
    return "external_positive"


def load_prompt_versions(path: Optional[Path] = None) -> dict[str, str]:
    versions_path = path or _PROMPT_VERSIONS_PATH
    versions: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        versions_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, version = line.partition(":")
        prompt_key = key.strip()
        prompt_version = version.strip()
        if not separator or prompt_key not in _PROMPT_KEYS or not prompt_version:
            raise ValueError(f"Invalid prompt version entry at {versions_path}:{line_number}")
        if prompt_key in versions:
            raise ValueError(f"Duplicate prompt version entry for {prompt_key}")
        versions[prompt_key] = prompt_version
    missing = _PROMPT_KEYS.difference(versions)
    if missing:
        raise ValueError(f"Missing prompt versions: {', '.join(sorted(missing))}")
    return versions


def prompt_version_for_key(prompt_key: str, path: Optional[Path] = None) -> str:
    normalized_key = prompt_key.strip().lower()
    if normalized_key not in _PROMPT_KEYS:
        raise ValueError(f"Unknown prompt key: {prompt_key}")
    return load_prompt_versions(path)[normalized_key]


def _get_prompt_path(prompt_key: str) -> Path:
    settings = get_settings()
    prompt_paths = {
        "external_positive": settings.external_filter_prompt_path,
        "external_negative": settings.external_negative_filter_prompt_path,
        "internal_positive": settings.internal_filter_prompt_path,
        "internal_negative": settings.internal_negative_filter_prompt_path,
    }
    return prompt_paths.get(prompt_key, settings.external_filter_prompt_path)


def _load_prompt_template(category: str = "external") -> str:
    prompt_key = prompt_key_for_category(category)
    if prompt_key in _PROMPT_CACHE:
        return _PROMPT_CACHE[prompt_key]
    prompt_path = _get_prompt_path(prompt_key)
    if not prompt_path.exists():
        _PROMPT_CACHE[prompt_key] = ""
        return _PROMPT_CACHE[prompt_key]
    content = prompt_path.read_text(encoding="utf-8")
    match = _PROMPT_TAG_PATTERN.search(content)
    template = match.group(1).strip() if match else content.strip()
    _PROMPT_CACHE[prompt_key] = template
    return template


def _truncate(text: str, limit: int = 1500) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "……（内容截断）"


def build_prompt(candidate: ExternalFilterCandidate, *, category: str = "external") -> str:
    template = _load_prompt_template(category)
    prompt_key = prompt_key_for_category(category)
    is_internal_category = prompt_key.startswith("internal")
    title = candidate.title or "（无标题）"
    source = candidate.source or "（未知来源）"
    summary = (candidate.summary or "").strip() or "（无摘要）"
    content = _truncate(candidate.content or "")
    keyword_section = ""
    if is_internal_category and candidate.keyword_matches:
        keyword_text = "、".join(candidate.keyword_matches)
        keyword_section = f"Bonus Keywords: {keyword_text}\n\n"
    return (
        f"{template}\n\n"
        f"{keyword_section}"
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
    started_at = time.monotonic()
    settings = get_settings()
    deadline = started_at + settings.llm_external_filter_budget
    api_key = settings.llm_api_key
    if not api_key:
        raise RuntimeError("Missing LLM API key (set LLM_API_KEY)")
    url = f"{settings.llm_api_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_external_filter_model,
        "messages": [{"role": "user", "content": build_prompt(candidate, category=category)}],
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
    resolved_timeout = timeout or settings.llm_external_filter_timeout

    def validate_response(data: dict[str, Any]) -> None:
        choice = data.get("choices", [{}])[0]
        if not extract_message_text(choice):
            raise RuntimeError("Empty response from external filter model")

    data = post_chat_completion(
        url,
        payload=payload,
        headers=headers,
        timeout=resolved_timeout,
        budget=settings.llm_external_filter_budget,
        retries=retries,
        retryable_statuses=_RETRYABLE_STATUS,
        operation=f"external_filter:{category}",
        model=settings.llm_external_filter_model,
        deadline=deadline,
        advance_backoff_on_exception=False,
        response_validator=validate_response,
    )
    choice = data.get("choices", [{}])[0]
    return extract_message_text(choice)


def parse_external_filter_score(raw_output: str) -> Optional[int]:
    return parse_score(raw_output)


__all__ = [
    "build_prompt",
    "call_external_filter_model",
    "load_prompt_versions",
    "parse_external_filter_score",
    "prompt_key_for_category",
    "prompt_version_for_key",
]
