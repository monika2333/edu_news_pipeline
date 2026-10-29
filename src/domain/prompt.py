from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# 提示词 key 是对外契约：文章 external_importance_raw.prompt_version 与
# score_feedbacks / 聚类反馈统计都按 key + version 绑定评分上下文，改名会让
# 历史反馈失去关联。新增 key 时同时补 PromptDefinition 与调用方。
PROMPT_KEYS = (
    "external_positive",
    "external_negative",
    "internal_positive",
    "internal_negative",
    "beijing_gate",
)
SCORING_PROMPT_KEYS = frozenset(PROMPT_KEYS[:4])

# 控制台保存时的边界。现有最长提示词约 15k 字符，50k 留足空间同时挡住误粘贴整篇文档。
MAX_PROMPT_CONTENT_LENGTH = 50_000
MAX_PROMPT_NOTE_LENGTH = 200

# 只有这四个评分提示词的版本会写进文章；京内判定结果目前不绑定提示词版本，
# 但同样进 prompt_revisions 以便回看与恢复。
PROMPT_KIND_SCORE = "score"
PROMPT_KIND_DECISION = "decision"


@dataclass(frozen=True)
class PromptDefinition:
    key: str
    label: str
    description: str
    step: str
    kind: str
    filename: str
    versioned_in_file: bool


PROMPT_DEFINITIONS = (
    PromptDefinition(
        key="external_positive",
        label="京外正面重要性",
        description="评估京外正面新闻对北京教育系统的参考价值，输出 0-100 分。",
        step="external_filter",
        kind=PROMPT_KIND_SCORE,
        filename="external_positive_importance_prompt.md",
        versioned_in_file=True,
    ),
    PromptDefinition(
        key="external_negative",
        label="京外负面重要性",
        description="评估京外负面新闻对北京教育系统的风险预警与治理参考价值，输出 0-100 分。",
        step="external_filter",
        kind=PROMPT_KIND_SCORE,
        filename="external_negative_importance_prompt.md",
        versioned_in_file=True,
    ),
    PromptDefinition(
        key="internal_positive",
        label="京内正面重要性",
        description="评估京内正面新闻对北京市教育决策与管理的参考价值，输出 0-100 分。",
        step="external_filter",
        kind=PROMPT_KIND_SCORE,
        filename="internal_positive_importance_prompt.md",
        versioned_in_file=True,
    ),
    PromptDefinition(
        key="internal_negative",
        label="京内负面重要性",
        description="评估京内负面新闻对北京市教工委的风险提示与整改价值，输出 0-100 分。",
        step="external_filter",
        kind=PROMPT_KIND_SCORE,
        filename="internal_negative_importance_prompt.md",
        versioned_in_file=True,
    ),
    PromptDefinition(
        key="beijing_gate",
        label="京内二次判定",
        description="判断稿件是否仍应归入北京分类，输出 JSON 结论与一句话理由。",
        step="beijing_gate",
        kind=PROMPT_KIND_DECISION,
        filename="beijing_gate_prompt.md",
        versioned_in_file=False,
    ),
)
PROMPT_BY_KEY = {definition.key: definition for definition in PROMPT_DEFINITIONS}


@dataclass(frozen=True)
class PromptTemplate:
    """One resolved prompt: database revision when present, file fallback otherwise."""

    key: str
    version: str
    content: str
    source: str
    revision: Optional[int] = None
    revision_id: Optional[int] = None
    updated_at: Optional[datetime] = None
    updated_by_user_id: Optional[str] = None
    updated_by_display_name: Optional[str] = None

    @property
    def is_database(self) -> bool:
        return self.source == "database"


def prompt_definition(key: str) -> PromptDefinition:
    try:
        return PROMPT_BY_KEY[str(key).strip().lower()]
    except KeyError as exc:
        raise KeyError(f"未知提示词：{key}") from exc


def normalize_prompt_content(content: str) -> str:
    """Normalize and validate one prompt body.

    Lives in ``src.domain`` because both the console save path and the nightly
    prompt optimizer must enforce exactly the same boundaries. The error
    messages are part of the console contract and intentionally unchanged.
    """

    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("提示词内容不能为空")
    if len(text) > MAX_PROMPT_CONTENT_LENGTH:
        raise ValueError(
            f"提示词内容不能超过 {MAX_PROMPT_CONTENT_LENGTH} 个字符（当前 {len(text)}）"
        )
    lowered = text.lower()
    if "<prompt>" in lowered or "</prompt>" in lowered:
        raise ValueError("只填写 <prompt> 标签内的正文，不要包含标签本身")
    return text


def format_prompt_version(revision: int) -> str:
    return f"v{int(revision)}"


def parse_prompt_version(value: str) -> Optional[int]:
    """Parse the historical ``vN`` label into a revision number."""

    text = str(value or "").strip().lower()
    if not text.startswith("v"):
        return None
    try:
        revision = int(text[1:])
    except ValueError:
        return None
    return revision if revision > 0 else None


__all__ = [
    "MAX_PROMPT_CONTENT_LENGTH",
    "MAX_PROMPT_NOTE_LENGTH",
    "PROMPT_BY_KEY",
    "PROMPT_DEFINITIONS",
    "PROMPT_KEYS",
    "PROMPT_KIND_DECISION",
    "PROMPT_KIND_SCORE",
    "PromptDefinition",
    "PromptTemplate",
    "SCORING_PROMPT_KEYS",
    "format_prompt_version",
    "normalize_prompt_content",
    "parse_prompt_version",
    "prompt_definition",
]
