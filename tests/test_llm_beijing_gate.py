from src.adapters import llm_beijing_gate as gate
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
    candidate = _candidate(title="北京高校招生", source="北京日报")
    prompt = gate.build_prompt(candidate)
    assert "北京高校招生" in prompt
    assert "北京日报" in prompt
    assert "情感标签" in prompt


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
