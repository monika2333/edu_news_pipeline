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
# R（参照集）允许被改坏的条数 = max(1, floor(|R| * 10%))，且必须少于修好计权分。
R_BROKEN_RATIO = 0.10
R_BROKEN_MIN_ALLOWANCE = 1
# 波动容差：2026-10-31 生产库实测（81 篇各重打 2 遍，加上运行记录共 3 个分数），
# 约 90% 文章三次分数最大分差在 0–4 分，5–9 分近乎空档。分数朝某方向变动不足
# 5 分视为正常抖动，不作为修好 / 改坏的证据。
NOISE_BAND = 5
# 证据计权（2026-10-31 用户拍板的初始值）：评分标注是人工给出的更强证据，修好
# 1 条记 2 分；报送存档来源修好记 1 分。计权只用于「改坏须少于修好」这一条判定，
# 其余门槛仍按条数计。
FEEDBACK_FIXED_WEIGHT = 2

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
CASE_ORIGIN_FEEDBACK = "feedback"
CASE_ORIGIN_SUBMISSION = "submission"
CASE_BUCKET_T = "T"
CASE_BUCKET_R = "R"
CASE_BUCKET_IGNORED = "ignored"
# 案例在候选版本下的最终判定（运行记录 cases JSON 的 fix_state 字段）。
FIX_STATE_FIXED = "fixed"
FIX_STATE_BROKEN = "broken"
FIX_STATE_FLUCTUATION = "fluctuation"
FIX_STATE_UNCHANGED = "unchanged"


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
    current_confirm_score: Optional[int] = None
    candidate_confirm_score: Optional[int] = None
    fix_state: Optional[str] = None

    @property
    def direction_label(self) -> str:
        return "调高" if self.direction == DIRECTION_UP else "调低"

    @property
    def fix_baseline_score(self) -> Optional[float]:
        """T 案例衡量涨幅的起点：两次当前版本回放中更接近达标的那次。

        调高方向取较大值、调低方向取较小值，防止把一次偏低的抽样当作起点、
        虚增候选版本的涨幅。R 案例的改坏起点直接用 ``current_score``，不走这里。
        """

        if self.current_score is None:
            return None
        if self.current_confirm_score is None:
            return float(self.current_score)
        pair = (self.current_score, self.current_confirm_score)
        return float(max(pair)) if self.direction == DIRECTION_UP else float(min(pair))

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
    weighted_fixed_score: int
    fluctuation_count: int
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


def _opposite_direction(direction: str) -> str:
    if direction == DIRECTION_UP:
        return DIRECTION_DOWN
    if direction == DIRECTION_DOWN:
        return DIRECTION_UP
    raise ValueError(f"未知调整方向：{direction}")


def moved_at_least(
    direction: str,
    baseline: Optional[float],
    score: Optional[int],
) -> bool:
    """分数相对 baseline 朝 direction 方向变动至少 NOISE_BAND 分才算「真的变了」。"""

    if baseline is None or score is None:
        return False
    delta = float(score) - float(baseline)
    if direction == DIRECTION_UP:
        return delta >= NOISE_BAND
    if direction == DIRECTION_DOWN:
        return -delta >= NOISE_BAND
    raise ValueError(f"未知调整方向：{direction}")


def case_fixed_probe(case: OptimizationCase, score: Optional[int]) -> bool:
    """候选分数是否满足「修好」的单次条件：达标且相对原分数涨幅过容差。"""

    return case_satisfied(
        case.direction,
        case.threshold,
        score,
    ) and moved_at_least(case.direction, case.fix_baseline_score, score)


def case_broken_probe(case: OptimizationCase, score: Optional[int]) -> bool:
    """候选分数是否满足「改坏」的单次条件：不达标且相对当前分反向跌过容差。"""

    return not case_satisfied(
        case.direction,
        case.threshold,
        score,
    ) and moved_at_least(
        _opposite_direction(case.direction),
        case.current_score,
        score,
    )


def resolve_case_state(
    case: OptimizationCase,
    first_score: Optional[int],
    second_score: Optional[int],
    *,
    confirmed: bool,
) -> str:
    """判定一条案例在候选版本下的最终状态（fix_state）。

    修好 / 改坏都要求单次条件连续两次满足：第一次不满足记「未变化」；
    第一次满足而第二次不满足（或没有发起确认）记「波动」；两次都满足才
    记「修好」/「改坏」。
    """

    if case.bucket == CASE_BUCKET_T:
        if not case_fixed_probe(case, first_score):
            return FIX_STATE_UNCHANGED
        if not confirmed:
            return FIX_STATE_FLUCTUATION
        return (
            FIX_STATE_FIXED
            if case_fixed_probe(case, second_score)
            else FIX_STATE_FLUCTUATION
        )
    if case.bucket == CASE_BUCKET_R:
        if not case_broken_probe(case, first_score):
            return FIX_STATE_UNCHANGED
        if not confirmed:
            return FIX_STATE_FLUCTUATION
        return (
            FIX_STATE_BROKEN
            if case_broken_probe(case, second_score)
            else FIX_STATE_FLUCTUATION
        )
    return FIX_STATE_UNCHANGED


def r_broken_allowance(reference_count: int) -> int:
    return max(
        R_BROKEN_MIN_ALLOWANCE,
        int(math.floor(reference_count * R_BROKEN_RATIO)),
    )


def _changed_char_count(old: str, new: str) -> int:
    """Count actually added/deleted characters, ignoring shared anchor text.

    Trim the common prefix and suffix first. This is the口径 promised to the
    model: inserting 162 characters after a 240-character anchor changes 162
    characters, not 642.
    """

    limit = min(len(old), len(new))
    prefix = 0
    while prefix < limit and old[prefix] == new[prefix]:
        prefix += 1
    suffix = 0
    while (
        suffix < limit - prefix
        and old[len(old) - 1 - suffix] == new[len(new) - 1 - suffix]
    ):
        suffix += 1
    return (len(old) - prefix - suffix) + (len(new) - prefix - suffix)


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
    the sum of each edit's actually added and deleted characters (shared
    prefix/suffix excluded), matching the meta-prompt's 800-character budget.
    """

    if not edits:
        raise PromptEditError("优化模型没有返回任何改动")
    if len(edits) > max_edits:
        raise PromptEditError(f"单次最多允许 {max_edits} 处替换，实际 {len(edits)} 处")
    changed_chars = sum(
        _changed_char_count(edit.old, edit.new) for edit in edits
    )
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
    candidate_confirm_scores: Optional[Mapping[str, Optional[int]]] = None,
) -> CheckResult:
    """Apply the acceptance gates: noise band, double confirmation, weighting.

    ``candidate_confirm_scores`` 只包含实际发起过确认回放的案例（值为第二次候选
    分数；值为 ``None`` 表示确认回放未解析出分数，按未解析处理）。修好 / 改坏
    的判定见 ``resolve_case_state``；三条门槛中「T 至少修好一半且至少 1 条」与
    「R 改坏不超过 max(1, ⌊|R|×10%⌋)」按条数计，「改坏须少于修好」按证据计权
    （评分标注每条修好记 ``FEEDBACK_FIXED_WEIGHT`` 分、报送存档记 1 分）。
    """

    confirms = dict(candidate_confirm_scores or {})
    t_total = len(target_cases)
    r_total = len(reference_cases)
    all_cases = list(target_cases) + list(reference_cases)
    unparseable = [
        case
        for case in all_cases
        if candidate_scores.get(case.article_id) is None
        or (
            case.article_id in confirms
            and confirms[case.article_id] is None
        )
    ]
    unparseable_ids = {case.article_id for case in unparseable}
    fixed_cases: list[OptimizationCase] = []
    broken_cases: list[OptimizationCase] = []
    fluctuation = 0
    for case in all_cases:
        if case.article_id in unparseable_ids:
            continue
        confirmed = case.article_id in confirms
        state = resolve_case_state(
            case,
            candidate_scores.get(case.article_id),
            confirms.get(case.article_id) if confirmed else None,
            confirmed=confirmed,
        )
        if state == FIX_STATE_FIXED:
            fixed_cases.append(case)
        elif state == FIX_STATE_BROKEN:
            broken_cases.append(case)
        elif state == FIX_STATE_FLUCTUATION:
            fluctuation += 1

    fixed = len(fixed_cases)
    broken = len(broken_cases)
    weighted_fixed = sum(
        FEEDBACK_FIXED_WEIGHT
        if case.origin == CASE_ORIGIN_FEEDBACK
        else 1
        for case in fixed_cases
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
    if broken >= weighted_fixed:
        failures.append(
            f"参照集 R 改坏 {broken} 条，不少于修好计权分 {weighted_fixed}"
        )

    return CheckResult(
        passed=not failures,
        fixed_count=fixed,
        broken_count=broken,
        weighted_fixed_score=weighted_fixed,
        fluctuation_count=fluctuation,
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
    "FEEDBACK_FIXED_WEIGHT",
    "FEEDBACK_SCORE_MARGIN",
    "FIX_STATE_BROKEN",
    "FIX_STATE_FIXED",
    "FIX_STATE_FLUCTUATION",
    "FIX_STATE_UNCHANGED",
    "NOISE_BAND",
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
    "case_broken_probe",
    "case_fixed_probe",
    "case_satisfied",
    "evaluate_candidate_check",
    "feedback_criterion",
    "moved_at_least",
    "r_broken_allowance",
    "resolve_case_state",
]
