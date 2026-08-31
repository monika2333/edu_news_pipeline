from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.adapters import llm_beijing_gate as gate
from src.config import get_settings
from src.domain import BeijingGateCandidate


def _candidate(**overrides):
    base = dict(
        article_id="test-1",
        title="北京教育改革发布",
        source="新华社",
        publish_time_iso=None,
        summary="北京市发布最新教育改革方案。",
        content="北京市教委今日发布最新教育改革方案。",
        sentiment_label="positive",
        is_beijing_related=True,
        is_beijing_related_llm=None,
        external_importance_status="pending_beijing_gate",
        beijing_gate_fail_count=0,
        beijing_gate_attempted_at=None,
    )
    base.update(overrides)
    return BeijingGateCandidate(**base)


def test_build_prompt_includes_core_fields():
    candidate = _candidate(title="北京高校招生", summary="摘要信息")
    prompt = gate.build_prompt(candidate)
    assert "北京高校招生" in prompt
    assert "摘要信息" in prompt
    assert "情感标签：" not in prompt


def test_prompt_path_comes_from_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt_path = tmp_path / "beijing_gate.md"
    prompt_path.write_text("<prompt>自定义北京判定提示词</prompt>", encoding="utf-8")
    settings = replace(get_settings(), beijing_gate_prompt_path=prompt_path)
    monkeypatch.setattr(gate, "get_settings", lambda: settings)
    monkeypatch.setattr(gate, "_PROMPT_CACHE", None)

    assert gate._load_prompt_template() == "自定义北京判定提示词"


def test_parse_decision_with_valid_json():
    raw = '{"is_beijing_related": true, "reason": "文章明确来自北京市教委。"}'
    result = gate._parse_decision(raw)
    assert result["is_beijing_related"] is True
    assert "北京市" in result["reason"]


def test_parse_decision_with_embedded_json():
    raw = "LLM Answer:\n\n```json\n{\"is_beijing_related\": false, \"reason\": \"事件发生在外省\"}\n```"
    result = gate._parse_decision(raw)
    assert result["is_beijing_related"] is False
    assert "外省" in result["reason"]


def test_parse_decision_with_text_fallback_true():
    raw = "判断：是，北京市教委主导。"
    result = gate._parse_decision(raw)
    assert result["is_beijing_related"] is True


def test_parse_decision_with_text_fallback_false():
    raw = "结论：否，与北京无关。"
    result = gate._parse_decision(raw)
    assert result["is_beijing_related"] is False


def test_parse_decision_rejects_valid_json_with_wrong_contract_field():
    raw = '{"is_behind_related": true, "reason": "活动在北京举行。"}'

    result = gate._parse_decision(raw)

    assert result["is_beijing_related"] is None
    assert result["reason"] == "活动在北京举行。"


def test_call_beijing_gate_uses_json_schema_and_retries_indeterminate_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            gate.BeijingGateResponse(
                raw_text='{"is_behind_related": true, "reason": "字段错误"}',
                provider="provider-a",
                model="model-a",
            ),
            gate.BeijingGateResponse(
                raw_text='{"is_beijing_related": true, "reason": "活动在北京举行"}',
                provider="provider-b",
                model="model-b",
            ),
        ]
    )
    calls: list[tuple[dict[str, object], int, float]] = []

    def fake_post(
        payload: dict[str, object],
        retries: int,
        timeout: int,
        *,
        deadline: float,
    ) -> gate.BeijingGateResponse:
        assert timeout > 0
        assert deadline > 0
        calls.append((payload, retries, deadline))
        return next(responses)

    monkeypatch.setattr(gate, "_post_chat_completion", fake_post)

    decision = gate.call_beijing_gate(_candidate(), retries=3)

    assert decision.is_beijing_related is True
    assert decision.attempts == 2
    assert decision.provider == "provider-b"
    assert decision.model == "model-b"
    assert [retries for _, retries, _ in calls] == [3, 1]
    assert calls[0][2] == calls[1][2]
    response_format = calls[0][0]["response_format"]
    assert isinstance(response_format, dict)
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True


def test_call_beijing_gate_preserves_final_indeterminate_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_response = gate.BeijingGateResponse(
        raw_text='{"is_behind_related": true, "reason": "字段错误"}',
        provider="provider-a",
        model="model-a",
    )
    monkeypatch.setattr(
        gate,
        "_post_chat_completion",
        lambda payload, retries, timeout, *, deadline: invalid_response,
    )

    with pytest.raises(gate.BeijingGateIndeterminateError) as exc_info:
        gate.call_beijing_gate(_candidate(), retries=2)

    payload = exc_info.value.diagnostic_payload()
    assert payload == {
        "model_output": invalid_response.raw_text,
        "provider": "provider-a",
        "model": "model-a",
        "semantic_attempts": 2,
    }
