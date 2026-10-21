from __future__ import annotations

import logging
import time
from typing import Any, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.external_filter_model import (
    call_external_filter_model,
    parse_external_filter_score,
)
from src.adapters.llm_beijing_gate import (
    BeijingGateIndeterminateError,
    call_beijing_gate,
)
from src.adapters.prompt_store import load_active_prompt
from src.business_config import get_llm_step_config
from src.config import get_settings
from src.console.auth_service import ConsoleUser
from src.console.prompt_service import normalize_prompt_content
from src.domain.external_filter import (
    BeijingGateCandidate,
    ExternalFilterCandidate,
    determine_candidate_category,
)
from src.domain.prompt import PROMPT_KIND_SCORE, prompt_definition

logger = logging.getLogger(__name__)


class PromptTestArticleNotFoundError(KeyError):
    """Raised when the article is not in ``news_summaries``."""


class PromptTestArticleUnavailableError(ValueError):
    """Raised when the article exists but lacks the content needed for a test."""


def _require_admin(actor: ConsoleUser) -> None:
    if actor.role != "admin" or not actor.user_id:
        raise PermissionError("提示词试跑必须使用已登录的管理员账号")


def _score_threshold(prompt_key: str) -> int:
    settings = get_settings()
    thresholds = {
        "external_positive": settings.external_filter_threshold,
        "external_negative": settings.external_filter_negative_threshold,
        "internal_positive": settings.internal_filter_threshold,
        "internal_negative": settings.internal_filter_negative_threshold,
    }
    return int(thresholds[prompt_key])


def _base_article_payload(
    row: dict[str, Any],
    *,
    category: str,
) -> dict[str, Any]:
    return {
        "article_id": str(row.get("article_id") or ""),
        "title": row.get("title"),
        "source": row.get("source"),
        "category": category,
        "status": row.get("status"),
    }


def _score_candidate(row: dict[str, Any]) -> ExternalFilterCandidate:
    return ExternalFilterCandidate(
        article_id=str(row.get("article_id") or ""),
        title=row.get("title"),
        source=row.get("source"),
        publish_time_iso=row.get("publish_time_iso"),
        summary=str(row.get("llm_summary") or ""),
        content=str(row.get("content_markdown") or ""),
        sentiment_label=row.get("sentiment_label"),
        is_beijing_related=row.get("is_beijing_related"),
        is_beijing_related_llm=row.get("is_beijing_related_llm"),
        external_importance_status=(
            str(row.get("external_importance_status") or "pending_external_filter")
        ),
        keyword_matches=tuple(row.get("keyword_matches") or ()),
    )


def _gate_candidate(row: dict[str, Any]) -> BeijingGateCandidate:
    return BeijingGateCandidate(
        article_id=str(row.get("article_id") or ""),
        title=row.get("title"),
        source=row.get("source"),
        publish_time_iso=row.get("publish_time_iso"),
        summary=str(row.get("llm_summary") or ""),
        content=str(row.get("content_markdown") or ""),
        sentiment_label=row.get("sentiment_label"),
        is_beijing_related=row.get("is_beijing_related"),
        is_beijing_related_llm=row.get("is_beijing_related_llm"),
        external_importance_status=(
            str(row.get("external_importance_status") or "pending_beijing_gate")
        ),
    )


def _current_score_context(row: dict[str, Any]) -> dict[str, Any]:
    raw_context = row.get("external_importance_raw")
    if not isinstance(raw_context, dict):
        raw_context = {}
    score_value = row.get("external_importance_score")
    return {
        "score": float(score_value) if score_value is not None else None,
        "prompt_key": raw_context.get("prompt_key"),
        "prompt_version": raw_context.get("prompt_version"),
        "category": raw_context.get("category"),
        "status": row.get("external_importance_status") or row.get("status"),
        "checked_at": row.get("external_importance_checked_at"),
    }


def _current_gate_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "is_beijing_related": row.get("is_beijing_related"),
        "is_beijing_related_llm": row.get("is_beijing_related_llm"),
        "status": row.get("status"),
        "checked_at": row.get("beijing_gate_checked_at"),
    }


def _run_score_test(
    row: dict[str, Any],
    *,
    prompt_key: str,
    content: str,
) -> dict[str, Any]:
    candidate = _score_candidate(row)
    settings = get_settings()
    model = get_llm_step_config("external_filter").model
    started = time.monotonic()
    raw_output = ""
    error: Optional[str] = None
    score: Optional[int] = None
    try:
        raw_output = call_external_filter_model(
            candidate,
            category=prompt_key,
            retries=max(1, settings.external_filter_max_retries),
            prompt_template=content,
        )
        score = parse_external_filter_score(raw_output)
    except Exception as exc:  # LLM/网络错误要回显给管理员，不写库
        error = str(exc) or exc.__class__.__name__
    elapsed_ms = int(round((time.monotonic() - started) * 1000))
    if score is None and error is None:
        error = "模型未返回可解析的分数"
    threshold = _score_threshold(prompt_key)
    passed = None if score is None else score >= threshold
    return {
        "score": score,
        "threshold": threshold,
        "passed": passed,
        "decision": None,
        "raw_output": raw_output,
        "error": error,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "article": _base_article_payload(
            row,
            category=candidate.candidate_category,
        ),
        "current": _current_score_context(row),
    }


def _run_gate_test(row: dict[str, Any], *, content: str) -> dict[str, Any]:
    candidate = _gate_candidate(row)
    settings = get_settings()
    model = get_llm_step_config("beijing_gate").model
    started = time.monotonic()
    raw_output = ""
    error: Optional[str] = None
    decision_payload: Optional[dict[str, Any]] = None
    try:
        decision = call_beijing_gate(
            candidate,
            retries=max(1, settings.beijing_gate_max_retries),
            prompt_template=content,
        )
        raw_output = decision.raw_text or ""
        decision_payload = {
            "is_beijing_related": decision.is_beijing_related,
            "reason": decision.reason,
            "attempts": decision.attempts,
            "provider": decision.provider,
            "model": decision.model,
        }
    except BeijingGateIndeterminateError as exc:
        raw_output = exc.raw_text or ""
        diagnostics = exc.diagnostic_payload()
        decision_payload = {
            "is_beijing_related": None,
            "reason": None,
            "attempts": diagnostics.get("semantic_attempts"),
            "provider": diagnostics.get("provider"),
            "model": diagnostics.get("model"),
        }
        error = "模型未返回可解析的京内判定"
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
    elapsed_ms = int(round((time.monotonic() - started) * 1000))
    category = determine_candidate_category(
        row.get("is_beijing_related"),
        row.get("sentiment_label"),
    )
    return {
        "score": None,
        "threshold": None,
        "passed": None,
        "decision": decision_payload,
        "raw_output": raw_output,
        "error": error,
        "model": model,
        "elapsed_ms": elapsed_ms,
        "article": _base_article_payload(row, category=category),
        "current": _current_gate_context(row),
    }


def test_prompt(
    prompt_key: str,
    *,
    article_id: str,
    content: Optional[str],
    actor: ConsoleUser,
) -> dict[str, Any]:
    """Run one article through one prompt without writing any pipeline state."""

    definition = prompt_definition(prompt_key)
    _require_admin(actor)
    adapter = get_adapter()
    current_prompt = load_active_prompt(definition.key, adapter)

    draft = False
    prompt_content = current_prompt.content
    prompt_version: Optional[str] = current_prompt.version
    if content is not None:
        prompt_content = normalize_prompt_content(content)
        if prompt_content != current_prompt.content:
            draft = True
            prompt_version = None
    if not prompt_content.strip():
        raise ValueError("提示词内容为空，无法试跑")

    safe_article_id = str(article_id or "").strip()
    if not safe_article_id:
        raise ValueError("article_id 不能为空")
    row = adapter.news_summaries.fetch_prompt_test_article(safe_article_id)
    if row is None:
        raise PromptTestArticleNotFoundError(
            f"文章不在摘要库中：{safe_article_id}"
        )
    if not str(row.get("content_markdown") or "").strip():
        raise PromptTestArticleUnavailableError("该文章尚无正文，无法试跑")

    if definition.kind == PROMPT_KIND_SCORE:
        result = _run_score_test(
            row,
            prompt_key=definition.key,
            content=prompt_content,
        )
    else:
        result = _run_gate_test(row, content=prompt_content)

    logger.info(
        "用户 %s 试跑提示词 %s（文章 %s，版本 %s）",
        actor.display_name or actor.username,
        definition.key,
        safe_article_id,
        "草稿" if draft else prompt_version,
    )
    return {
        "prompt_key": definition.key,
        "prompt_label": definition.label,
        "kind": definition.kind,
        "draft": draft,
        "prompt_version": prompt_version,
        **result,
    }


__all__ = [
    "PromptTestArticleNotFoundError",
    "PromptTestArticleUnavailableError",
    "test_prompt",
]
