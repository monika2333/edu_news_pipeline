from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from src.adapters.llm_chat import extract_message_text, post_chat_completion
from src.adapters.llm_endpoint import resolve_llm_endpoint
from src.business_config import get_llm_step_config
from src.config import get_settings
from src.domain.prompt_edit import PromptEdit

PROMPT_OPTIMIZER_META_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "prompts" / "prompt_optimizer.md"
)
PROMPT_OPTIMIZER_STEP = "prompt_optimizer"
PROMPT_OPTIMIZER_NOTE_LIMIT = 200
DECISION_CHANGE = "change"
DECISION_NO_CHANGE = "no_change"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_JSON_OBJECT_PATTERN = re.compile(r"\{.*\}", re.DOTALL)


class PromptOptimizerResponseError(ValueError):
    """Raised when the optimizer output is not the required JSON shape."""


@dataclass(frozen=True)
class PromptOptimizerDecision:
    decision: str
    edits: tuple[PromptEdit, ...]
    note: str
    raw_output: str
    model: str

    @property
    def changed(self) -> bool:
        return self.decision == DECISION_CHANGE


def load_prompt_optimizer_meta(path: Optional[Path] = None) -> str:
    return (path or PROMPT_OPTIMIZER_META_PATH).read_text(encoding="utf-8").strip()


def _strip_code_fence(raw: str) -> str:
    text = str(raw or "").strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_prompt_optimizer_response(
    raw_output: str,
    *,
    model: str = "",
) -> PromptOptimizerDecision:
    text = _strip_code_fence(raw_output)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_PATTERN.search(text)
        if match is None:
            raise PromptOptimizerResponseError("优化模型没有返回 JSON 对象") from None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise PromptOptimizerResponseError(
                f"优化模型返回的 JSON 无法解析：{exc.msg}"
            ) from exc
    if not isinstance(payload, Mapping):
        raise PromptOptimizerResponseError("优化模型输出必须是 JSON 对象")

    raw_decision = str(payload.get("decision") or "").strip().lower()
    aliases = {
        "change": DECISION_CHANGE,
        "改": DECISION_CHANGE,
        "no_change": DECISION_NO_CHANGE,
        "no-change": DECISION_NO_CHANGE,
        "unchanged": DECISION_NO_CHANGE,
        "不改": DECISION_NO_CHANGE,
    }
    decision = aliases.get(raw_decision)
    if decision is None:
        raise PromptOptimizerResponseError(
            "decision 必须是 change 或 no_change"
        )
    raw_note = payload.get("note")
    if raw_note is None:
        note = ""
    elif isinstance(raw_note, str):
        note = raw_note.strip()
    else:
        raise PromptOptimizerResponseError("note 必须是字符串")
    if len(note) > PROMPT_OPTIMIZER_NOTE_LIMIT:
        raise PromptOptimizerResponseError(
            f"note 不能超过 {PROMPT_OPTIMIZER_NOTE_LIMIT} 字"
        )

    raw_edits = payload.get("edits", [])
    if raw_edits is None:
        raw_edits = []
    if not isinstance(raw_edits, list):
        raise PromptOptimizerResponseError("edits 必须是数组")
    edits: list[PromptEdit] = []
    for index, item in enumerate(raw_edits, start=1):
        if not isinstance(item, Mapping):
            raise PromptOptimizerResponseError(f"edits[{index}] 必须是对象")
        old = item.get("old")
        new = item.get("new")
        if not isinstance(old, str) or not old:
            raise PromptOptimizerResponseError(f"edits[{index}].old 必须是非空字符串")
        if not isinstance(new, str):
            raise PromptOptimizerResponseError(f"edits[{index}].new 必须是字符串")
        edits.append(PromptEdit(old=old, new=new))
    if decision == DECISION_CHANGE and not edits:
        raise PromptOptimizerResponseError("decision=change 时 edits 不能为空")
    if decision == DECISION_NO_CHANGE and edits:
        raise PromptOptimizerResponseError("decision=no_change 时 edits 必须为空")
    return PromptOptimizerDecision(
        decision=decision,
        edits=tuple(edits),
        note=note,
        raw_output=str(raw_output or ""),
        model=model,
    )


def format_optimizer_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    header: str,
) -> str:
    lines = [header]
    for index, case in enumerate(cases, start=1):
        lines.append(f"案例 {index}")
        lines.append(f"标题：{case.get('title') or '（无标题）'}")
        lines.append(f"来源：{case.get('source') or '（未知）'}")
        if case.get("summary"):
            lines.append(f"摘要：{case['summary']}")
        if case.get("content_excerpt"):
            lines.append(f"正文节选：{case['content_excerpt']}")
        lines.append(f"当前分数：{case.get('current_score', case.get('stored_score'))}")
        lines.append(f"期望方向：{case.get('direction_label') or case.get('direction')}")
        lines.append(f"达标判据：{case.get('criterion_label') or ''}")
        if case.get("reason"):
            lines.append(f"人工原因：{case['reason']}")
        if case.get("origin_label"):
            lines.append(f"来源类型：{case['origin_label']}")
    return "\n".join(lines)


def build_optimizer_user_prompt(
    *,
    prompt_key: str,
    current_content: str,
    cases: Sequence[Mapping[str, Any]],
    reference_cases: Sequence[Mapping[str, Any]],
    failure_feedback: Optional[str] = None,
    meta_prompt: Optional[str] = None,
) -> str:
    meta = meta_prompt if meta_prompt is not None else load_prompt_optimizer_meta()
    parts = [
        meta,
        f"\n\n【提示词 key】{prompt_key}",
        "\n【当前提示词开始】\n"
        + current_content
        + "\n【当前提示词结束】",
        "\n\n"
        + format_optimizer_cases(
            cases,
            header="【需要修正的案例（T）】",
        ),
    ]
    if reference_cases:
        parts.append(
            "\n\n"
            + format_optimizer_cases(
                reference_cases,
                header="【当前版本下已达标、不能改坏的参照案例（R，仅列少量）】",
            )
        )
    if failure_feedback:
        parts.append(
            "\n\n【上一次候选版本未通过自动检查】\n" + failure_feedback
        )
    parts.append(
        "\n\n请只返回 JSON。不要复述完整提示词，只返回最小 edits。"
    )
    return "".join(parts)


def call_prompt_optimizer(
    *,
    prompt_key: str,
    current_content: str,
    cases: Sequence[Mapping[str, Any]],
    reference_cases: Sequence[Mapping[str, Any]],
    failure_feedback: Optional[str] = None,
) -> PromptOptimizerDecision:
    settings = get_settings()
    step_config = get_llm_step_config(PROMPT_OPTIMIZER_STEP)
    endpoint = resolve_llm_endpoint(PROMPT_OPTIMIZER_STEP)
    payload = {
        "model": step_config.model,
        "messages": [
            {
                "role": "user",
                "content": build_optimizer_user_prompt(
                    prompt_key=prompt_key,
                    current_content=current_content,
                    cases=cases,
                    reference_cases=reference_cases,
                    failure_feedback=failure_feedback,
                ),
            }
        ],
        "temperature": 0.0,
    }
    endpoint.finalize_payload(
        payload,
        settings=settings,
        reasoning_enabled=step_config.reasoning,
    )

    def validate_response(data: dict[str, Any]) -> None:
        choice = data.get("choices", [{}])[0]
        if not extract_message_text(choice):
            raise RuntimeError("Empty response from prompt optimizer model")

    data = post_chat_completion(
        endpoint.chat_url,
        payload=payload,
        headers=endpoint.headers(),
        timeout=settings.llm_prompt_optimizer_timeout,
        budget=settings.llm_prompt_optimizer_budget,
        retries=2,
        retryable_statuses=_RETRYABLE_STATUS,
        operation=f"prompt_optimizer:{prompt_key}",
        model=step_config.model,
        deadline=time.monotonic() + settings.llm_prompt_optimizer_budget,
        advance_backoff_on_exception=False,
        response_validator=validate_response,
        endpoint_label=endpoint.label,
    )
    raw_output = extract_message_text(data.get("choices", [{}])[0])
    return parse_prompt_optimizer_response(
        raw_output,
        model=step_config.model,
    )


__all__ = [
    "DECISION_CHANGE",
    "DECISION_NO_CHANGE",
    "PROMPT_OPTIMIZER_META_PATH",
    "PROMPT_OPTIMIZER_STEP",
    "PromptOptimizerDecision",
    "PromptOptimizerResponseError",
    "build_optimizer_user_prompt",
    "call_prompt_optimizer",
    "format_optimizer_cases",
    "load_prompt_optimizer_meta",
    "parse_prompt_optimizer_response",
]
