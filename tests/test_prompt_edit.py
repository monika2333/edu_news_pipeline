from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from src.domain.prompt_edit import (
    CASE_BUCKET_R,
    CASE_BUCKET_T,
    CASE_ORIGIN_FEEDBACK,
    CASE_ORIGIN_SUBMISSION,
    DIRECTION_DOWN,
    DIRECTION_UP,
    FIX_STATE_BROKEN,
    FIX_STATE_FIXED,
    FIX_STATE_FLUCTUATION,
    FIX_STATE_UNCHANGED,
    NOISE_BAND,
    OptimizationCase,
    PromptEdit,
    PromptEditError,
    apply_prompt_edits,
    case_broken_probe,
    case_fixed_probe,
    case_satisfied,
    evaluate_candidate_check,
    feedback_criterion,
    moved_at_least,
    resolve_case_state,
)


def _case(
    article_id: str,
    direction: str = DIRECTION_UP,
    threshold: float = 60,
    *,
    origin: str = CASE_ORIGIN_FEEDBACK,
    bucket: Optional[str] = None,
    current_score: Optional[int] = None,
    current_confirm_score: Optional[int] = None,
) -> OptimizationCase:
    case = OptimizationCase(
        article_id=article_id,
        title=f"标题-{article_id}",
        source="来源",
        origin=origin,
        direction=direction,
        threshold=threshold,
        original_score=50,
        prompt_key="external_positive",
        event_at=datetime.now(timezone.utc),
    )
    case.bucket = bucket
    case.current_score = current_score
    case.current_confirm_score = current_confirm_score
    return case


def _t_case(
    article_id: str,
    *,
    direction: str = DIRECTION_UP,
    threshold: float = 60,
    current_score: int = 50,
    current_confirm_score: int = 50,
    origin: str = CASE_ORIGIN_FEEDBACK,
) -> OptimizationCase:
    return _case(
        article_id,
        direction,
        threshold,
        origin=origin,
        bucket=CASE_BUCKET_T,
        current_score=current_score,
        current_confirm_score=current_confirm_score,
    )


def _r_case(
    article_id: str,
    *,
    direction: str = DIRECTION_UP,
    threshold: float = 60,
    current_score: int = 60,
) -> OptimizationCase:
    return _case(
        article_id,
        direction,
        threshold,
        origin=CASE_ORIGIN_SUBMISSION,
        bucket=CASE_BUCKET_R,
        current_score=current_score,
    )


def test_replacement_insertion_and_deletion_are_applied_in_sequence() -> None:
    content = "甲\n乙\n丙"

    result = apply_prompt_edits(
        content,
        [
            PromptEdit(old="乙", new="乙\n新增规则"),
            PromptEdit(old="甲", new="甲改"),
            PromptEdit(old="丙", new=""),
        ],
    )

    assert result == "甲改\n乙\n新增规则"


def test_old_must_exist_exactly_once() -> None:
    with pytest.raises(PromptEditError, match="实际出现 0 次"):
        apply_prompt_edits("abc", [PromptEdit(old="x", new="y")])

    with pytest.raises(PromptEditError, match="实际出现 2 次"):
        apply_prompt_edits("aba", [PromptEdit(old="a", new="c")])


def test_duplicate_anchor_created_by_earlier_edit_invalidates_attempt() -> None:
    with pytest.raises(PromptEditError, match="实际出现 2 次"):
        apply_prompt_edits(
            "锚点",
            [
                PromptEdit(old="锚点", new="锚点\n锚点"),
                PromptEdit(old="锚点", new="改"),
            ],
        )


def test_edit_count_and_total_changed_chars_are_capped() -> None:
    with pytest.raises(PromptEditError, match="最多允许 3 处"):
        apply_prompt_edits(
            "a b c d",
            [PromptEdit(old=item, new=f"{item}!") for item in ("a", "b", "c", "d")],
        )

    with pytest.raises(PromptEditError, match="改动总量"):
        apply_prompt_edits(
            "a",
            [PromptEdit(old="a", new="x" * 900)],
        )


def test_insertion_budget_counts_only_actually_added_characters() -> None:
    anchor = "锚" * 700
    added = "新" * 162
    content = f"开头\n{anchor}\n结尾"

    result = apply_prompt_edits(
        content,
        [PromptEdit(old=anchor, new=anchor + added)],
    )

    assert result == f"开头\n{anchor}{added}\n结尾"
    # 旧口径会把锚点算两遍（700 + 862 = 1562），误判超限。
    assert len(result) - len(content) == 162


def test_noop_and_empty_edits_are_rejected() -> None:
    with pytest.raises(PromptEditError, match="没有返回任何改动"):
        apply_prompt_edits("abc", [])
    with pytest.raises(PromptEditError, match="空改动"):
        apply_prompt_edits("abc", [PromptEdit(old="abc", new="abc")])


def test_result_still_uses_normalize_prompt_content_validation() -> None:
    with pytest.raises(ValueError, match="不要包含标签"):
        apply_prompt_edits(
            "abc",
            [PromptEdit(old="abc", new="<prompt>abc</prompt>")],
        )
    with pytest.raises(ValueError, match="不能超过"):
        apply_prompt_edits(
            "a" * 49_999 + "XYZ",
            [PromptEdit(old="XYZ", new="XYZa")],
        )


def test_feedback_criterion_uses_margin_and_clamps_to_score_range() -> None:
    assert feedback_criterion("too_low", 52) == (DIRECTION_UP, 62)
    assert feedback_criterion("too_low", 96) == (DIRECTION_UP, 100)
    assert feedback_criterion("too_high", 52) == (DIRECTION_DOWN, 42)
    assert feedback_criterion("too_high", 3) == (DIRECTION_DOWN, 0)


def test_case_satisfied_handles_none_and_direction() -> None:
    assert case_satisfied(DIRECTION_UP, 60, 60) is True
    assert case_satisfied(DIRECTION_UP, 60, 59) is False
    assert case_satisfied(DIRECTION_DOWN, 40, 40) is True
    assert case_satisfied(DIRECTION_DOWN, 40, 41) is False
    assert case_satisfied(DIRECTION_UP, 60, None) is False


def test_check_requires_each_replay_to_parse() -> None:
    target = _t_case("t1")
    scores = {"t1": None}
    result = evaluate_candidate_check([target], [], scores)
    assert result.passed is False
    assert result.unparseable_count == 1
    assert any("未能解析出分数" in item for item in result.failures)


def test_confirm_replay_unparseable_also_fails_the_check() -> None:
    target = _t_case("t1")
    result = evaluate_candidate_check(
        [target],
        [],
        {"t1": 70},
        {"t1": None},
    )
    assert result.passed is False
    assert result.unparseable_count == 1
    assert any("未能解析出分数" in item for item in result.failures)


def test_check_requires_half_of_target_and_at_least_one_fixed() -> None:
    targets = [_t_case("t1"), _t_case("t2"), _t_case("t3")]
    scores = {"t1": 60, "t2": 50, "t3": 50}
    confirms = {"t1": 60}
    result = evaluate_candidate_check(targets, [], scores, confirms)
    assert result.fixed_count == 1
    assert result.passed is False
    scores = {"t1": 60, "t2": 60, "t3": 50}
    confirms = {"t1": 60, "t2": 60}
    assert evaluate_candidate_check(targets, [], scores, confirms).passed is True


def test_check_limits_reference_damage_to_ten_percent_and_below_fixed() -> None:
    target = _t_case("t1")
    references = [_r_case(f"r{index}", current_score=60) for index in range(20)]
    # 1 条标注修好（计权 2）+ 2 条改坏：R 允许 max(1, 2)=2 条，但改坏须少于修好计权分。
    scores = {"t1": 60}
    scores.update({f"r{index}": 60 for index in range(20)})
    scores.update({"r0": 50, "r1": 50})
    confirms = {"t1": 60, "r0": 50, "r1": 50}
    result = evaluate_candidate_check([target], references, scores, confirms)
    assert result.broken_count == 2
    assert result.weighted_fixed_score == 2
    assert result.passed is False
    assert any("不少于修好计权分 2" in item for item in result.failures)

    # 修好 3 条（计权 6）、改坏 2 条：R 配额内且少于修好计权分。
    targets = [_t_case("t1"), _t_case("t2"), _t_case("t3")]
    scores.update({"t2": 60, "t3": 60})
    confirms.update({"t2": 60, "t3": 60})
    result = evaluate_candidate_check(targets, references, scores, confirms)
    assert result.weighted_fixed_score == 6
    assert result.passed is True


def test_reference_broken_alone_cannot_pass_even_with_no_target_damage() -> None:
    target = _t_case("t1")
    references = [_r_case("r1", current_score=60), _r_case("r2", current_score=60)]
    result = evaluate_candidate_check(
        [target],
        references,
        {"t1": 60, "r1": 50, "r2": 50},
        {"t1": 60, "r1": 50, "r2": 50},
    )
    assert result.broken_count == 2
    assert result.passed is False


def test_bucket_constants_are_distinct() -> None:
    assert CASE_BUCKET_T != CASE_BUCKET_R


def test_noise_band_constant_matches_measurement() -> None:
    assert NOISE_BAND == 5


def test_moved_at_least_requires_band_sized_move_in_direction() -> None:
    assert moved_at_least(DIRECTION_UP, 50, 55) is True
    assert moved_at_least(DIRECTION_UP, 50, 54) is False
    assert moved_at_least(DIRECTION_DOWN, 55, 50) is True
    assert moved_at_least(DIRECTION_DOWN, 55, 51) is False
    # 朝反方向变动再多也不算
    assert moved_at_least(DIRECTION_UP, 55, 40) is False
    assert moved_at_least(DIRECTION_DOWN, 50, 65) is False
    # 没有分数或没有起点不构成变化
    assert moved_at_least(DIRECTION_UP, None, 60) is False
    assert moved_at_least(DIRECTION_UP, 50, None) is False


def test_fixed_requires_band_sized_move_up_and_down() -> None:
    # 调高方向：起点 56（两次当前回放 50/56 中更接近达标的那个），涨 4 不算修好。
    case = _t_case("t1", threshold=60, current_score=50, current_confirm_score=56)
    assert case.fix_baseline_score == 56
    assert case_fixed_probe(case, 60) is False
    assert resolve_case_state(case, 60, 60, confirmed=True) == FIX_STATE_UNCHANGED
    # 涨 5 才算修好，且要两次都满足
    assert case_fixed_probe(case, 61) is True
    assert resolve_case_state(case, 61, 61, confirmed=True) == FIX_STATE_FIXED
    assert resolve_case_state(case, 61, 58, confirmed=True) == FIX_STATE_FLUCTUATION

    # 调低方向：起点 44（两次当前回放 46/44 取较小值），降 4 不算、降 5 且两次才算。
    case = _t_case(
        "t2",
        direction=DIRECTION_DOWN,
        threshold=40,
        current_score=46,
        current_confirm_score=44,
    )
    assert case.fix_baseline_score == 44
    assert case_fixed_probe(case, 40) is False
    assert case_fixed_probe(case, 39) is True
    assert resolve_case_state(case, 39, 39, confirmed=True) == FIX_STATE_FIXED


def test_broken_requires_band_sized_opposite_move() -> None:
    # 调低案例的参照被抬高：当前 58（≤60 达标），候选 62 抬 4 分不算改坏。
    case = _r_case("r1", direction=DIRECTION_DOWN, threshold=60, current_score=58)
    assert case_broken_probe(case, 62) is False
    assert resolve_case_state(case, 62, 62, confirmed=True) == FIX_STATE_UNCHANGED
    # 抬 5 分且不达标、两次都满足才算改坏
    assert case_broken_probe(case, 63) is True
    assert resolve_case_state(case, 63, 63, confirmed=True) == FIX_STATE_BROKEN
    assert resolve_case_state(case, 63, 58, confirmed=True) == FIX_STATE_FLUCTUATION

    # 调高案例的参照被打低：当前 66，候选 59（跌 7 且不达标）算改坏候选。
    case = _r_case("r2", threshold=60, current_score=66)
    assert case_broken_probe(case, 59) is True
    assert resolve_case_state(case, 59, 59, confirmed=True) == FIX_STATE_BROKEN


def test_first_probe_without_confirmation_is_fluctuation_not_fixed() -> None:
    target = _t_case("t1")
    reference = _r_case("r1", current_score=66)
    assert (
        resolve_case_state(target, 70, None, confirmed=False)
        == FIX_STATE_FLUCTUATION
    )
    assert (
        resolve_case_state(reference, 59, None, confirmed=False)
        == FIX_STATE_FLUCTUATION
    )


def test_t_baseline_takes_score_closer_to_passing() -> None:
    # 调高方向取较大值：起点 62 而不是第一次的 55，候选 66 只涨 4 分不算修好。
    case = _t_case(
        "t1",
        threshold=65,
        current_score=55,
        current_confirm_score=62,
    )
    assert case.fix_baseline_score == 62
    assert case_fixed_probe(case, 66) is False

    # 调低方向取较小值：起点 40 而不是第一次的 45。
    case = _t_case(
        "t2",
        direction=DIRECTION_DOWN,
        threshold=35,
        current_score=45,
        current_confirm_score=40,
    )
    assert case.fix_baseline_score == 40


def test_weighted_fixed_feedback_counts_double_submission_single() -> None:
    # 1 条标注修好（计权 2）+ 1 条改坏：2 > 1，通过。
    target = _t_case("t1", origin=CASE_ORIGIN_FEEDBACK)
    reference = _r_case("r1", current_score=66)
    result = evaluate_candidate_check(
        [target],
        [reference],
        {"t1": 60, "r1": 59},
        {"t1": 60, "r1": 59},
    )
    assert result.fixed_count == 1
    assert result.broken_count == 1
    assert result.weighted_fixed_score == 2
    assert result.passed is True

    # 1 条存档修好（计权 1）+ 1 条改坏：1 不大于 1，不通过。
    target = _t_case("t1", origin=CASE_ORIGIN_SUBMISSION)
    result = evaluate_candidate_check(
        [target],
        [reference],
        {"t1": 60, "r1": 59},
        {"t1": 60, "r1": 59},
    )
    assert result.weighted_fixed_score == 1
    assert result.passed is False
    assert any("不少于修好计权分 1" in item for item in result.failures)


def test_half_of_target_gate_counts_cases_not_weight() -> None:
    # 3 条待修正只修好 1 条（标注，计权 2）：计权够、条数不够，仍不通过。
    targets = [_t_case("t1"), _t_case("t2"), _t_case("t3")]
    result = evaluate_candidate_check(
        targets,
        [],
        {"t1": 60, "t2": 50, "t3": 50},
        {"t1": 60},
    )
    assert result.fixed_count == 1
    assert result.weighted_fixed_score == 2
    assert result.passed is False
    assert any("只修好 1/3 条" in item for item in result.failures)


def test_fluctuation_count_aggregates_target_and_reference() -> None:
    targets = [
        _t_case("t1", current_score=50, current_confirm_score=50),
        _t_case("t2", current_score=50, current_confirm_score=50),
    ]
    references = [_r_case("r1", current_score=66), _r_case("r2", current_score=60)]
    result = evaluate_candidate_check(
        targets,
        references,
        {"t1": 70, "t2": 55, "r1": 59, "r2": 60},
        # t1 第二次不达标 → 波动；r1 第二次回达标 → 波动；t2、r2 未触发确认。
        {"t1": 52, "r1": 70},
    )
    assert result.fluctuation_count == 2
    assert result.fixed_count == 0
    assert result.broken_count == 0
