from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.domain.prompt_edit import (
    CASE_BUCKET_R,
    CASE_BUCKET_T,
    CASE_ORIGIN_FEEDBACK,
    DIRECTION_DOWN,
    DIRECTION_UP,
    OptimizationCase,
    PromptEdit,
    PromptEditError,
    apply_prompt_edits,
    case_satisfied,
    evaluate_candidate_check,
    feedback_criterion,
)


def _case(
    article_id: str,
    direction: str = DIRECTION_UP,
    threshold: float = 60,
) -> OptimizationCase:
    return OptimizationCase(
        article_id=article_id,
        title=f"标题-{article_id}",
        source="来源",
        origin=CASE_ORIGIN_FEEDBACK,
        direction=direction,
        threshold=threshold,
        original_score=50,
        prompt_key="external_positive",
        event_at=datetime.now(timezone.utc),
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
    target = _case("t1")
    scores = {"t1": None}
    result = evaluate_candidate_check([target], [], scores)
    assert result.passed is False
    assert result.unparseable_count == 1
    assert any("未能解析出分数" in item for item in result.failures)


def test_check_requires_half_of_target_and_at_least_one_fixed() -> None:
    targets = [_case("t1"), _case("t2"), _case("t3")]
    result = evaluate_candidate_check(targets, [], {"t1": 60, "t2": 50, "t3": 50})
    assert result.fixed_count == 1
    assert result.passed is False
    scores = {"t1": 60, "t2": 60, "t3": 50}
    assert evaluate_candidate_check(targets, [], scores).passed is True


def test_check_limits_reference_damage_to_ten_percent_and_below_fixed() -> None:
    targets = [_case("t1"), _case("t2")]
    references = [_case(f"r{index}") for index in range(20)]
    # 2 条修好、2 条改坏：R 允许 max(1, 2)=2 条，但必须少于修好条数。
    scores = {"t1": 60, "t2": 60, "r0": 50, "r1": 50}
    for index in range(2, 20):
        scores[f"r{index}"] = 60
    result = evaluate_candidate_check(targets, references, scores)
    assert result.broken_count == 2
    assert result.passed is False
    assert any("不少于" in item for item in result.failures)

    # 修好 3 条、改坏 2 条：R 配额内且少于修好条数。
    targets = [_case("t1"), _case("t2"), _case("t3")]
    scores.update({"t1": 60, "t2": 60, "t3": 60})
    assert evaluate_candidate_check(targets, references, scores).passed is True


def test_reference_broken_alone_cannot_pass_even_with_no_target_damage() -> None:
    targets = [_case("t1")]
    references = [_case("r1"), _case("r2")]
    result = evaluate_candidate_check(
        targets,
        references,
        {"t1": 60, "r1": 50, "r2": 50},
    )
    assert result.broken_count == 2
    assert result.passed is False


def test_bucket_constants_are_distinct() -> None:
    assert CASE_BUCKET_T != CASE_BUCKET_R
