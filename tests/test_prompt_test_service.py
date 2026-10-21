from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest

from src.adapters.llm_beijing_gate import (
    BeijingGateIndeterminateError,
    BeijingGateResponse,
)
from src.console import prompt_test_service
from src.console.auth_service import ConsoleUser
from src.domain.prompt import PROMPT_KEYS

ADMIN = ConsoleUser(
    method="test",
    user_id="admin-id",
    username="admin",
    display_name="测试管理员",
    role="admin",
)
DUTY = ConsoleUser(
    method="test",
    user_id="duty-id",
    username="duty",
    display_name="值班编辑",
    role="duty_editor",
)


def _prompt_row(key: str) -> dict[str, Any]:
    return {
        "id": 12,
        "prompt_key": key,
        "revision": 2,
        "content": f"{key} body",
        "note": None,
        "created_at": None,
        "created_by_user_id": None,
        "created_by_display_name": None,
    }


def _article_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "article_id": "article-1",
        "title": "北京某中学开展课后服务",
        "source": "北京日报",
        "publish_time_iso": "2026-10-20T08:00:00+00:00",
        "llm_summary": "摘要内容",
        "content_markdown": "正文内容",
        "sentiment_label": "positive",
        "is_beijing_related": True,
        "is_beijing_related_llm": True,
        "external_importance_status": "ready_for_export",
        "external_importance_score": 72,
        "external_importance_raw": {
            "prompt_key": "internal_positive",
            "prompt_version": "v2",
            "category": "internal_positive",
        },
        "external_importance_checked_at": "2026-10-20T09:00:00+00:00",
        "beijing_gate_checked_at": "2026-10-20T08:30:00+00:00",
        "beijing_gate_raw": {"is_beijing_related": True},
        "keyword_matches": ("高考", "校园安全"),
        "status": "ready_for_export",
    }
    row.update(overrides)
    return row


class _FakeNewsSummaries:
    def __init__(self, row: Optional[dict[str, Any]]) -> None:
        self.row = row
        self.calls: list[str] = []

    def fetch_prompt_test_article(self, article_id: str) -> Optional[dict[str, Any]]:
        self.calls.append(article_id)
        return self.row


class _FakePromptRevisions:
    def fetch_latest_by_key(self) -> dict[str, dict[str, Any]]:
        return {key: _prompt_row(key) for key in PROMPT_KEYS}


class _FakeAdapter:
    def __init__(self, row: Optional[dict[str, Any]]) -> None:
        self.news_summaries = _FakeNewsSummaries(row)
        self.prompt_revisions = _FakePromptRevisions()


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        external_filter_threshold=60,
        external_filter_negative_threshold=30,
        internal_filter_threshold=60,
        internal_filter_negative_threshold=30,
        external_filter_max_retries=2,
        beijing_gate_max_retries=2,
    )


def _patch_common(monkeypatch, adapter=None) -> _FakeAdapter:
    resolved_adapter = adapter or _FakeAdapter(_article_row())
    monkeypatch.setattr(prompt_test_service, "get_adapter", lambda: resolved_adapter)
    monkeypatch.setattr(
        prompt_test_service,
        "get_llm_step_config",
        lambda step: SimpleNamespace(model=f"{step}-model"),
    )
    monkeypatch.setattr(prompt_test_service, "get_settings", _settings)

    def unexpected_llm(*args, **kwargs):
        raise AssertionError("test must not call a real LLM")

    monkeypatch.setattr(
        prompt_test_service,
        "call_external_filter_model",
        unexpected_llm,
    )
    monkeypatch.setattr(prompt_test_service, "call_beijing_gate", unexpected_llm)
    return resolved_adapter


def test_score_test_returns_score_threshold_and_current(monkeypatch) -> None:
    adapter = _patch_common(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_call(candidate, *, category, retries, prompt_template):
        captured.update(
            candidate=candidate,
            category=category,
            retries=retries,
            prompt_template=prompt_template,
        )
        return "78"

    monkeypatch.setattr(prompt_test_service, "call_external_filter_model", fake_call)

    result = prompt_test_service.test_prompt(
        "external_positive",
        article_id="article-1",
        content=None,
        actor=ADMIN,
    )

    assert result["score"] == 78
    assert result["threshold"] == 60
    assert result["passed"] is True
    assert result["draft"] is False
    assert result["prompt_version"] == "v2"
    assert result["model"] == "external_filter-model"
    assert result["article"]["category"] == "internal_positive"
    assert result["current"]["score"] == 72
    assert result["current"]["prompt_version"] == "v2"
    assert result["error"] is None
    assert captured["category"] == "external_positive"
    assert captured["retries"] == 2
    assert captured["prompt_template"] == "external_positive body"
    assert captured["candidate"].keyword_matches == ("高考", "校园安全")
    assert adapter.news_summaries.calls == ["article-1"]


def test_draft_content_is_used_and_labeled(monkeypatch) -> None:
    _patch_common(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_call(candidate, *, category, retries, prompt_template):
        captured["prompt_template"] = prompt_template
        return "80"

    monkeypatch.setattr(prompt_test_service, "call_external_filter_model", fake_call)

    result = prompt_test_service.test_prompt(
        "external_negative",
        article_id="article-1",
        content="  draft body\r\nline  ",
        actor=ADMIN,
    )

    assert result["draft"] is True
    assert result["prompt_version"] is None
    assert captured["prompt_template"] == "draft body\nline"
    assert result["threshold"] == 30


def test_content_equal_to_saved_prompt_is_not_labeled_draft(monkeypatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        prompt_test_service,
        "call_external_filter_model",
        lambda candidate, *, category, retries, prompt_template: "80",
    )

    result = prompt_test_service.test_prompt(
        "internal_positive",
        article_id="article-1",
        content="internal_positive body",
        actor=ADMIN,
    )

    assert result["draft"] is False
    assert result["prompt_version"] == "v2"


def test_unparseable_output_returns_error_and_raw(monkeypatch) -> None:
    _patch_common(monkeypatch)
    monkeypatch.setattr(
        prompt_test_service,
        "call_external_filter_model",
        lambda candidate, *, category, retries, prompt_template: "not a score",
    )

    result = prompt_test_service.test_prompt(
        "external_positive",
        article_id="article-1",
        content=None,
        actor=ADMIN,
    )

    assert result["score"] is None
    assert result["passed"] is None
    assert result["error"] == "模型未返回可解析的分数"
    assert result["raw_output"] == "not a score"


def test_model_exception_is_returned_to_caller(monkeypatch) -> None:
    _patch_common(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("upstream boom")

    monkeypatch.setattr(prompt_test_service, "call_external_filter_model", fail)

    result = prompt_test_service.test_prompt(
        "external_positive",
        article_id="article-1",
        content=None,
        actor=ADMIN,
    )

    assert result["score"] is None
    assert result["error"] == "upstream boom"
    assert result["raw_output"] == ""


def test_missing_article_raises_not_found(monkeypatch) -> None:
    _patch_common(monkeypatch, _FakeAdapter(None))

    with pytest.raises(prompt_test_service.PromptTestArticleNotFoundError):
        prompt_test_service.test_prompt(
            "external_positive",
            article_id="missing",
            content=None,
            actor=ADMIN,
        )


def test_empty_article_content_raises_unavailable(monkeypatch) -> None:
    _patch_common(
        monkeypatch,
        _FakeAdapter(_article_row(content_markdown="  ")),
    )

    with pytest.raises(prompt_test_service.PromptTestArticleUnavailableError):
        prompt_test_service.test_prompt(
            "external_positive",
            article_id="article-1",
            content=None,
            actor=ADMIN,
        )


def test_gate_test_returns_decision(monkeypatch) -> None:
    _patch_common(monkeypatch)
    captured: dict[str, Any] = {}

    def fake_gate(candidate, *, retries, prompt_template):
        captured["prompt_template"] = prompt_template
        captured["retries"] = retries
        return SimpleNamespace(
            is_beijing_related=True,
            reason="明确属于北京市范围",
            raw_text='{"is_beijing_related": true}',
            attempts=1,
            provider="provider-a",
            model="model-a",
        )

    monkeypatch.setattr(prompt_test_service, "call_beijing_gate", fake_gate)

    result = prompt_test_service.test_prompt(
        "beijing_gate",
        article_id="article-1",
        content=None,
        actor=ADMIN,
    )

    assert result["kind"] == "decision"
    assert result["score"] is None
    assert result["threshold"] is None
    assert result["passed"] is None
    assert result["decision"]["is_beijing_related"] is True
    assert result["decision"]["reason"] == "明确属于北京市范围"
    assert result["current"]["is_beijing_related"] is True
    assert result["model"] == "beijing_gate-model"
    assert captured == {
        "prompt_template": "beijing_gate body",
        "retries": 2,
    }


def test_gate_indeterminate_response_is_returned_with_error(monkeypatch) -> None:
    _patch_common(monkeypatch)
    error = BeijingGateIndeterminateError(
        BeijingGateResponse(
            raw_text='{"is_behind_related": true}',
            provider="provider-a",
            model="model-a",
        ),
        attempts=2,
    )

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(prompt_test_service, "call_beijing_gate", fail)

    result = prompt_test_service.test_prompt(
        "beijing_gate",
        article_id="article-1",
        content=None,
        actor=ADMIN,
    )

    assert result["decision"]["is_beijing_related"] is None
    assert result["error"] == "模型未返回可解析的京内判定"
    assert result["raw_output"] == '{"is_behind_related": true}'


def test_requires_admin(monkeypatch) -> None:
    _patch_common(monkeypatch)

    with pytest.raises(PermissionError):
        prompt_test_service.test_prompt(
            "external_positive",
            article_id="article-1",
            content=None,
            actor=DUTY,
        )


def test_unknown_prompt_key_is_rejected(monkeypatch) -> None:
    _patch_common(monkeypatch)

    with pytest.raises(KeyError):
        prompt_test_service.test_prompt(
            "unknown",
            article_id="article-1",
            content=None,
            actor=ADMIN,
        )
