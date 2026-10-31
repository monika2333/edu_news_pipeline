from __future__ import annotations

import json

import pytest

from src.adapters.llm_prompt_optimizer import (
    DECISION_CHANGE,
    DECISION_NO_CHANGE,
    PromptOptimizerResponseError,
    build_optimizer_user_prompt,
    load_prompt_optimizer_meta,
    parse_prompt_optimizer_response,
)


def test_meta_prompt_file_is_present_and_demands_structured_json() -> None:
    meta = load_prompt_optimizer_meta()
    assert "最小" in meta
    assert '"decision"' in meta
    assert "<prompt>" in meta


def test_meta_prompt_positive_example_avoids_archive_criteria_words() -> None:
    """✓ 示例行不得出现「存档 / 回链 / 报送」类判据字样（2026-10-31 拍板）。

    模型会模仿示例的写法；示例里出现判据字样会诱导它把验收判据写进评分
    提示词（首版 internal_negative 的「报送存档…不低于 60」硬保底即由此而来）。
    """

    meta = load_prompt_optimizer_meta()
    example_lines = [
        line for line in meta.splitlines() if line.lstrip().startswith("- ✓")
    ]
    assert example_lines, "元提示词应至少保留一条 ✓ 示例行"
    for line in example_lines:
        for word in ("存档", "回链", "报送"):
            assert word not in line, f"✓ 示例行不得包含判据字样「{word}」：{line}"


def test_parse_change_response_with_fenced_json() -> None:
    raw = """```json
{"decision":"change","edits":[{"old":"A","new":"A\\n新增"}],"note":"补规则"}
```"""

    decision = parse_prompt_optimizer_response(raw, model="m")

    assert decision.decision == DECISION_CHANGE
    assert decision.changed is True
    assert len(decision.edits) == 1
    assert decision.edits[0].old == "A"
    assert decision.edits[0].new == "A\n新增"
    assert decision.note == "补规则"
    assert decision.model == "m"


def test_parse_no_change_allows_empty_edits() -> None:
    decision = parse_prompt_optimizer_response(
        '{"decision":"no_change","edits":[],"note":"证据不足"}'
    )
    assert decision.decision == DECISION_NO_CHANGE
    assert decision.edits == ()


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("not json", "没有返回 JSON"),
        ('{"decision":"maybe","edits":[]}', "decision 必须是"),
        ('{"decision":"change","edits":[]}', "edits 不能为空"),
        (
            '{"decision":"no_change","edits":[{"old":"A","new":"B"}]}',
            "edits 必须为空",
        ),
        (
            '{"decision":"change","edits":[{"old":"","new":"B"}]}',
            "old 必须是非空字符串",
        ),
        (
            '{"decision":"change","edits":[{"old":"A","new":3}]}',
            "new 必须是字符串",
        ),
    ],
)
def test_parse_rejects_malformed_contract(raw: str, message: str) -> None:
    with pytest.raises(PromptOptimizerResponseError, match=message):
        parse_prompt_optimizer_response(raw)


def test_overlong_note_is_truncated_instead_of_rejecting_the_output() -> None:
    decision = parse_prompt_optimizer_response(
        json.dumps(
            {
                "decision": "no_change",
                "edits": [],
                "note": "x" * 201,
            }
        )
    )

    assert len(decision.note) == 200
    assert decision.note == "x" * 200


def test_user_prompt_contains_cases_current_content_and_retry_reason() -> None:
    prompt = build_optimizer_user_prompt(
        prompt_key="external_positive",
        current_content="CURRENT PROMPT",
        cases=[
            {
                "title": "某高校新闻",
                "source": "来源",
                "summary": "摘要",
                "content_excerpt": "正文",
                "current_score": 30,
                "direction_label": "调高",
                "criterion_label": "回放分 ≥ 60",
                "reason": "应该高分",
                "origin_label": "报送存档（zongbao）",
            }
        ],
        reference_cases=[
            {
                "title": "不能改坏的新闻",
                "current_score": 80,
                "direction_label": "调高",
                "criterion_label": "回放分 ≥ 60",
            }
        ],
        failure_feedback="上次把 R 改坏了 2 条",
        meta_prompt="META",
    )

    assert "META" in prompt
    assert "CURRENT PROMPT" in prompt
    assert "某高校新闻" in prompt
    assert "应该高分" in prompt
    assert "不能改坏的新闻" in prompt
    assert "上次把 R 改坏了 2 条" in prompt
    assert "请只返回 JSON" in prompt
