from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Sequence

from src.domain.prompt import normalize_prompt_content

# 夜间自动优化的初始校准值，集中放在这里，跑两周后按运行记录再调。
# 来源：委托书「评分提示词夜间自动优化」2.2 / 2.4 / 2.5（2026-10-29）。
OPTIMIZATION_LOOKBACK_DAYS = 30
OPTIMIZATION_MAX_CASES = 90
OPTIMIZATION_MAX_ATTEMPTS = 2
FEEDBACK_SCORE_MARGIN = 10.0
SUBMITTED_SCORE_FLOOR = 60.0
PROMPT_EDIT_MAX_EDITS = 3
PROMPT_EDIT_MAX_CHANGED_CHARS = 800
# R（参照集）允许被改坏的条数 = max(1, floor(|R| * 10%))，且必须少于修好的条数。
R_BROKEN_RATIO = 0.10
R_BROKEN_MIN_ALLOWANCE = 1

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
CASE_ORIGIN_FEEDBACK = "feedback"
CASE_ORIGIN_SUBMISSION = "submission"
CASE_BUCKET_T = "T"
CASE_BUCKET_R = "R"
CASE_BUCKET_IGNORED = "ignored"


class PromptEditError(ValueError):
    """Raised when the optimizer's edits cannot be applied safely."""


@dataclass(frozen=True, slots=True)
class PromptEdit:
    old: str
    new: str


@dataclass(slots=True)
class OptimizationCase:
    """One replayable article that expresses an expected scoring direction."""

    article_id: str
    title: str
    source: Optional[str]
    origin: str
    direction: str
    threshold: float
    original_score: Optional[float]
    prompt_key: str
    event_at: datetime
    reason: Optional[str] = None
    feedback_type: Optional[str] = None
    report_types: tuple[str, ...] = ()
    stored_score: Optional[float] = None
    summary: str = ""
    content_excerpt: str = ""
    skip_reason: Optional[str] = None
    replay_error: Optional[str] = None
    bucket: Optional[str] = None
    current_score: Optional[int] = None
    candidate_score: Optional[int] = None
    current_satisfied: Optional[bool] = None
    candidate_satisfied: Optional[bool] = None

    @property
    def direction_label(self) -> str:
        return "调高" if self.direction == DIRECTION_UP else "调低"

    @property
    def criterion_label(self) -> str:
        operator = "≥" if self.direction == DIRECTION_UP else "≤"
        return f"回放分 {operator} {self.threshold:g}"

    @property
    def origin_label(self) -> str:
        if self.origin == CASE_ORIGIN_FEEDBACK:
            return "评分标注·偏低" if self.direction == DIRECTION_UP else "评分标注·偏高"
        reports = " / ".join(self.report_types) or "报送存档"
        return f"报送存档（{reports}）"


@dataclass(frozen=True, slots=True)
class CheckResult:
    passed: bool
    fixed_count: int
    broken_count: int
    t_total: int
    r_total: int
    unparseable_count: int
    failures: tuple[str, ...]


def feedback_criterion(
    feedback_type: str,
    score_value: float,
) -> tuple[str, float]:
    """Map one human feedback direction to a replay criterion."""

    if feedback_type == "too_low":
        return DIRECTION_UP, min(float(score_value) + FEEDBACK_SCORE_MARGIN, 100.0)
    if feedback_type == "too_high":
        return DIRECTION_DOWN, max(float(score_value) - FEEDBACK_SCORE_MARGIN, 0.0)
    raise ValueError(f"未知评分标注类型：{feedback_type}")


def case_satisfied(
    direction: str,
    threshold: float,
    score: Optional[int],
) -> bool:
    if score is None:
        return False
    if direction == DIRECTION_UP:
        return score >= threshold
    if direction == DIRECTION_DOWN:
        return score <= threshold
    raise ValueError(f"未知调整方向：{direction}")


def r_broken_allowance(reference_count: int) -> int:
    return max(
        R_BROKEN_MIN_ALLOWANCE,
        int(math.floor(reference_count * R_BROKEN_RATIO)),
    )


def apply_prompt_edits(
    content: str,
    edits: Sequence[PromptEdit],
    *,
    max_edits: int = PROMPT_EDIT_MAX_EDITS,
    max_changed_chars: int = PROMPT_EDIT_MAX_CHANGED_CHARS,
) -> str:
    """Apply exact, non-overlapping text edits and validate the result.

    ``old`` must occur exactly once in the text at the moment it is applied.
    Insertions are represented by replacing a unique anchor with anchor + new
    text, so no separate insert operation is needed. The total changed size is
    the sum of every old and new string length, which is easy to audit.
    """

    if not edits:
        raise PromptEditError("优化模型没有返回任何改动")
    if len(edits) > max_edits:
        raise PromptEditError(f"单次最多允许 {max_edits} 处替换，实际 {len(edits)} 处")
    changed_chars = sum(len(edit.old) + len(edit.new) for edit in edits)
    if changed_chars > max_changed_chars:
        raise PromptEditError(
            f"改动总量 {changed_chars} 字超过上限 {max_changed_chars} 字"
        )

    result = str(content or "")
    for index, edit in enumerate(edits, start=1):
        old = edit.old
        new = edit.new
        if not old:
            raise PromptEditError(f"第 {index} 处替换的 old 不能为空")
        occurrences = result.count(old)
        if occurrences != 1:
            raise PromptEditError(
                f"第 {index} 处替换的 old 必须在当前提示词中恰好出现一次，"
                f"实际出现 {occurrences} 次"
            )
        if old == new:
            raise PromptEditError(f"第 {index} 处替换的 old 与 new 相同，属于空改动")
        result = result.replace(old, new, 1)

    return normalize_prompt_content(result)


def evaluate_candidate_check(
    target_cases: Sequence[OptimizationCase],
    reference_cases: Sequence[OptimizationCase],
    candidate_scores: Mapping[str, Optional[int]],
) -> CheckResult:
    """Apply the three acceptance gates from the delegation, step 2.4.5."""

    t_total = len(target_cases)
    r_total = len(reference_cases)
    all_cases = list(target_cases) + list(reference_cases)
    unparseable = [
        case
        for case in all_cases
        if candidate_scores.get(case.article_id) is None
    ]
    fixed = sum(
        1
        for case in target_cases
        if case_satisfied(
            case.direction,
            case.threshold,
            candidate_scores.get(case.article_id),
        )
    )
    broken = sum(
        1
        for case in reference_cases
        if not case_satisfied(
            case.direction,
            case.threshold,
            candidate_scores.get(case.article_id),
        )
    )
    required_fixed = math.ceil(t_total / 2) if t_total else 0
    broken_allowance = r_broken_allowance(r_total)

    failures: list[str] = []
    if unparseable:
        failures.append(
            f"候选版本有 {len(unparseable)} 条回放未能解析出分数"
        )
    if fixed < required_fixed or fixed < 1:
        failures.append(
            f"待修正集 T 只修好 {fixed}/{t_total} 条，要求至少 "
            f"{max(1, required_fixed)} 条"
        )
    if broken > broken_allowance:
        failures.append(
            f"参照集 R 改坏 {broken}/{r_total} 条，允许最多 {broken_allowance} 条"
        )
    if broken >= fixed:
        failures.append(
            f"参照集 R 改坏 {broken} 条，不少于 T 修好的 {fixed} 条"
        )

    return CheckResult(
        passed=not failures,
        fixed_count=fixed,
        broken_count=broken,
        t_total=t_total,
        r_total=r_total,
        unparseable_count=len(unparseable),
        failures=tuple(failures),
    )


__all__ = [
    "CASE_BUCKET_IGNORED",
    "CASE_BUCKET_R",
    "CASE_BUCKET_T",
    "CASE_ORIGIN_FEEDBACK",
    "CASE_ORIGIN_SUBMISSION",
    "DIRECTION_DOWN",
    "DIRECTION_UP",
    "CheckResult",
    "FEEDBACK_SCORE_MARGIN",
    "OPTIMIZATION_LOOKBACK_DAYS",
    "OPTIMIZATION_MAX_ATTEMPTS",
    "OPTIMIZATION_MAX_CASES",
    "OptimizationCase",
    "PROMPT_EDIT_MAX_CHANGED_CHARS",
    "PROMPT_EDIT_MAX_EDITS",
    "PromptEdit",
    "PromptEditError",
    "R_BROKEN_MIN_ALLOWANCE",
    "R_BROKEN_RATIO",
    "SUBMITTED_SCORE_FLOOR",
    "apply_prompt_edits",
    "case_satisfied",
    "evaluate_candidate_check",
    "feedback_criterion",
    "r_broken_allowance",
]
