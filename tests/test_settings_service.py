from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.config import get_settings
from src.console import manual_filter_duplicate_service, settings_service
from src.console.app import create_app
from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user


def _admin() -> ConsoleUser:
    return ConsoleUser(
        method="test",
        user_id="00000000-0000-0000-0000-000000000301",
        username="admin",
        display_name="管理员",
        role="admin",
    )


def _llm_value(*, summary: str | None = None) -> dict[str, Any]:
    return {
        "default": "default-model",
        "steps": {
            "summary": {"model": summary, "reasoning": False},
            "source": {"model": None, "reasoning": True},
            "sentiment": {"model": None, "reasoning": True},
            "scoring": {"model": None, "reasoning": True},
            "external_filter": {"model": None, "reasoning": True},
            "beijing_gate": {"model": None, "reasoning": True},
            "duplicate_review": {"model": None, "reasoning": True},
        },
    }


def test_m3_console_duplicate_review_reads_current_model_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    models = iter(["duplicate-model-a", "duplicate-model-b"])
    monkeypatch.setattr(
        manual_filter_duplicate_service,
        "get_model_for_step",
        lambda step: next(models),
    )
    review_loader = lambda *_args, **_kwargs: {
        "total": 1,
        "items": [{"article_id": "article-1"}],
    }

    first = manual_filter_duplicate_service.check_duplicates(
        report_type="zongbao",
        decision="selected",
        review_loader=review_loader,
    )
    second = manual_filter_duplicate_service.check_duplicates(
        report_type="zongbao",
        decision="selected",
        review_loader=review_loader,
    )

    assert first["model"] == "duplicate-model-a"
    assert second["model"] == "duplicate-model-b"


def test_m14_import_refuses_parse_errors_before_opening_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings_service, "load_environment", lambda: None)
    for key in (
        "TOUTIAO_AUTHORS_PATH",
        "TENCENT_AUTHORS_PATH",
        "BTIME_UIDS_PATH",
        "BEIJINGHAO_COLUMNS_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "btime_author.txt").write_text("not-a-uid\n", encoding="utf-8")
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: pytest.fail("invalid preview must not open database"),
    )

    with pytest.raises(ValueError, match="无法解析"):
        settings_service.import_legacy_config(apply=True, root=tmp_path)


@pytest.mark.parametrize("reasoning", [True, False])
def test_model_test_uses_requested_reasoning_in_payload(
    monkeypatch: pytest.MonkeyPatch,
    reasoning: bool,
) -> None:
    settings = replace(get_settings(), llm_api_key="test-key")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    monkeypatch.setattr(
        settings_service,
        "post_chat_completion",
        lambda _url, **kwargs: captured.update(kwargs["payload"]) or {},
    )

    result = settings_service.test_llm_model(
        "summary",
        "model-a",
        reasoning,
    )

    assert result["success"] is True
    assert ("reasoning" in captured) is reasoning


@pytest.mark.parametrize(
    "source",
    ["bjrb", "laodongwubao", "ldwb", "beijingdaily"],
)
def test_m7_daily_only_sources_and_aliases_return_422(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin

    response = TestClient(app).put(
        "/api/admin/settings/crawl_sources",
        json={"value": [source], "expected_version": 1},
    )

    assert response.status_code == 422


def test_m16_changed_model_is_tested_server_side_before_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    app_config = SimpleNamespace(
        fetch_setting=lambda section: {
            "section": section,
            "value": _llm_value(),
            "version": 4,
        }
    )
    adapter = SimpleNamespace(
        app_config=app_config,
        update_app_setting_as_user=lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        settings_service,
        "test_llm_model",
        lambda step, model, reasoning: {
            "success": False,
            "elapsed_ms": 1,
            "error": f"{step}:{model}:{reasoning}:unavailable",
        },
    )

    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin
    response = TestClient(app).put(
        "/api/admin/settings/llm_models",
        json={
            "value": _llm_value(summary="new-summary-model"),
            "expected_version": 4,
        },
    )

    assert response.status_code == 422
    assert "摘要生成模型测试失败" in response.json()["detail"]
    assert calls == []


def test_m16_reasoning_only_change_is_tested_before_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _llm_value()
    after = _llm_value()
    after["steps"]["summary"]["reasoning"] = True
    tests: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    adapter = SimpleNamespace(
        app_config=SimpleNamespace(
            fetch_setting=lambda section: {
                "section": section,
                "value": before,
                "version": 4,
            }
        ),
        update_app_setting_as_user=lambda **kwargs: saves.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        settings_service,
        "test_llm_model",
        lambda step, model, reasoning: (
            tests.append((step, model, reasoning))
            or {"success": True, "elapsed_ms": 1, "error": None}
        ),
    )

    settings_service.update_setting(
        "llm_models",
        value=after,
        expected_version=4,
        actor=_admin(),
    )

    assert tests == [("summary", "default-model", True)]
    assert len(saves) == 1


def test_m16_default_change_tests_all_distinct_effective_combinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _llm_value()
    after = _llm_value()
    after["default"] = "new-default-model"
    tests: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    adapter = SimpleNamespace(
        app_config=SimpleNamespace(
            fetch_setting=lambda section: {
                "section": section,
                "value": before,
                "version": 4,
            }
        ),
        update_app_setting_as_user=lambda **kwargs: saves.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)
    monkeypatch.setattr(
        settings_service,
        "test_llm_model",
        lambda step, model, reasoning: (
            tests.append((step, model, reasoning))
            or {"success": True, "elapsed_ms": 1, "error": None}
        ),
    )

    settings_service.update_setting(
        "llm_models",
        value=after,
        expected_version=4,
        actor=_admin(),
    )

    assert tests == [
        ("summary", "new-default-model", False),
        ("source", "new-default-model", True),
    ]
    assert len(saves) == 1


def test_legacy_import_preserves_each_reasoning_switch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(settings_service, "load_environment", lambda: None)
    monkeypatch.setenv("LLM_REASONING_ENABLED", "false")
    monkeypatch.setenv("LLM_SUMMARY_REASONING_ENABLED", "true")
    monkeypatch.setenv("LLM_SOURCE_REASONING_ENABLED", "false")
    monkeypatch.setenv("LLM_SENTIMENT_REASONING_ENABLED", "true")

    value = settings_service.preview_legacy_import(root=tmp_path)["sections"][
        "llm_models"
    ]

    assert value["steps"]["summary"]["reasoning"] is True
    assert value["steps"]["source"]["reasoning"] is False
    assert value["steps"]["sentiment"]["reasoning"] is True
    for step in ("scoring", "external_filter", "beijing_gate", "duplicate_review"):
        assert value["steps"][step]["reasoning"] is False


def test_m19_account_preview_classifies_all_four_statuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config = SimpleNamespace(
        fetch_accounts=lambda source: [
            {"source": source, "normalized_identifier": "999"}
        ]
    )
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: SimpleNamespace(app_config=app_config),
    )

    items = settings_service.preview_accounts(
        "btime",
        "123\n123\nnot-a-uid\n999\n",
    )

    assert [item["status"] for item in items] == [
        "addable",
        "batch_duplicate",
        "invalid",
        "existing_duplicate",
    ]
    assert items[2]["error"]
