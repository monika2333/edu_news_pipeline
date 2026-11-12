from __future__ import annotations

import json
import os
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.adapters import db_postgres_core, llm_duplicate_review
from src.adapters.account_profiles import (
    MAX_ACCOUNT_TIMEOUT_SECONDS,
    AccountNameUnavailable,
)
from src.adapters.http_toutiao import PROFILE_NAME_TIMEOUT_SECONDS
from src.business_config import LLM_API_KEY_ENV_PATTERN, validate_llm_models
from src.config import get_settings, load_environment
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


def _llm_endpoints_value() -> dict[str, Any]:
    return {
        "default": "openrouter",
        "items": [
            {
                "key": "openrouter",
                "label": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "LLM_API_KEY",
                "api_style": "openrouter",
                "temperature_override": None,
                "default_model": "default-model",
            },
            {
                "key": "deepseek",
                "label": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "LLM_DEEPSEEK_API_KEY",
                "api_style": "thinking",
                "temperature_override": None,
                "default_model": "deepseek-default",
            },
        ],
    }


def _wordlist_rows() -> list[dict[str, Any]]:
    return [
        {
            "section": "score_keyword_bonuses",
            "value": [{"keyword": "教育工委", "bonus": 100}],
            "version": 1,
        },
        {"section": "score_exclusion_keywords", "value": [], "version": 1},
        {"section": "education_keywords", "value": ["教育"], "version": 1},
        {"section": "beijing_keywords", "value": ["北京"], "version": 1},
        {
            "section": "source_aliases",
            "value": {"suffixes": [], "aliases": {}},
            "version": 1,
        },
        {
            "section": "review_sort_keywords",
            "value": {"市教委": ["市教委"], "中小学": ["小学"], "高校": ["大学"]},
            "version": 1,
        },
    ]


def _settings_section_adapter(
    sections: dict[str, Any],
    *,
    versions: dict[str, int] | None = None,
    saves: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    version_map = versions or {section: 4 for section in sections}

    def fetch_setting(section: str) -> dict[str, Any]:
        return {
            "section": section,
            "value": sections[section],
            "version": version_map.get(section, 1),
        }

    def update_app_setting_as_user(**kwargs: Any) -> dict[str, Any]:
        if saves is not None:
            saves.append(kwargs)
        return kwargs

    return SimpleNamespace(
        app_config=SimpleNamespace(
            fetch_setting=fetch_setting,
            update_app_setting_as_user=update_app_setting_as_user,
        ),
    )


def _patch_settings_adapter(
    monkeypatch: pytest.MonkeyPatch,
    adapter: SimpleNamespace,
) -> None:
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)


def test_m3_console_duplicate_review_reads_current_model_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_value = _llm_value()
    current_value["steps"]["scoring"] = {
        "model": "scoring-model",
        "reasoning": True,
        "endpoint": "openrouter",
    }
    current_value["steps"]["duplicate_review"] = {
        "model": "duplicate-model-a",
        "reasoning": True,
        "endpoint": "deepseek",
    }
    requested_models: list[str] = []
    app_config = SimpleNamespace(
        fetch_settings=lambda: [
            {"section": "llm_models", "value": current_value, "version": 1},
            {"section": "llm_endpoints", "value": _llm_endpoints_value(), "version": 1},
            {"section": "crawl_sources", "value": ["toutiao"], "version": 1},
            *_wordlist_rows(),
        ],
        fetch_enabled_accounts=lambda: [],
    )
    adapter = SimpleNamespace(app_config=app_config)
    settings = replace(get_settings(), llm_api_key="test-key")
    monkeypatch.setattr(db_postgres_core, "get_adapter", lambda: adapter)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    # 查重复核现在指定 deepseek 接入点，需要它的 Key 环境变量
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(llm_duplicate_review, "get_settings", lambda: settings)
    monkeypatch.setattr(
        llm_duplicate_review,
        "post_chat_completion",
        lambda _url, **kwargs: (
            requested_models.append(kwargs["payload"]["model"])
            or {
                "choices": [
                    {"message": {"content": '{"duplicate_groups":[]}'}}
                ]
            }
        ),
    )
    review_loader = lambda *_args, **_kwargs: {
        "total": 2,
        "items": [
            {"article_id": "article-1"},
            {"article_id": "article-2"},
        ],
    }

    first = manual_filter_duplicate_service.check_duplicates(
        report_type="zongbao",
        decision="selected",
        review_loader=review_loader,
    )
    current_value = _llm_value()
    current_value["steps"]["scoring"] = {
        "model": "scoring-model",
        "reasoning": True,
        "endpoint": "openrouter",
    }
    current_value["steps"]["duplicate_review"] = {
        "model": "duplicate-model-b",
        "reasoning": True,
        "endpoint": "deepseek",
    }
    second = manual_filter_duplicate_service.check_duplicates(
        report_type="zongbao",
        decision="selected",
        review_loader=review_loader,
    )

    assert first["model"] == "duplicate-model-a"
    assert second["model"] == "duplicate-model-b"
    assert requested_models == ["duplicate-model-a", "duplicate-model-b"]


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
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter({"llm_endpoints": _llm_endpoints_value()}),
    )
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


def test_model_test_uses_requested_endpoint_address_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(get_settings(), llm_api_key="test-key")
    captured: dict[str, Any] = {}
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter({"llm_endpoints": _llm_endpoints_value()}),
    )
    monkeypatch.setattr(
        settings_service,
        "post_chat_completion",
        lambda url, **kwargs: captured.update(kwargs, url=url) or {},
    )

    result = settings_service.test_llm_model(
        "duplicate_review",
        "deepseek-chat",
        True,
        endpoint="deepseek",
    )

    assert result["success"] is True
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer deepseek-key"
    assert "HTTP-Referer" not in captured["headers"]
    assert captured["payload"]["thinking"] == {"type": "enabled"}
    assert "reasoning" not in captured["payload"]


def test_model_test_reports_missing_endpoint_key_without_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(get_settings(), llm_api_key="test-key")
    monkeypatch.delenv("LLM_GHOST_API_KEY", raising=False)
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    endpoints = _llm_endpoints_value()
    endpoints["items"].append(
        {
            "key": "ghost",
            "label": "Ghost",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "LLM_GHOST_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "ghost-model",
        }
    )
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter({"llm_endpoints": endpoints}),
    )

    result = settings_service.test_llm_model(
        "summary",
        "model-a",
        False,
        endpoint="ghost",
    )

    assert result["success"] is False
    assert "LLM_GHOST_API_KEY" in result["error"]


def test_f9_reasoning_model_test_has_room_beyond_reasoning_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        get_settings(),
        llm_api_key="test-key",
        llm_reasoning_max_tokens=3072,
        llm_scoring_timeout=30,
    )
    captured: dict[str, Any] = {}
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter({"llm_endpoints": _llm_endpoints_value()}),
    )
    monkeypatch.setattr(
        settings_service,
        "post_chat_completion",
        lambda _url, **kwargs: captured.update(kwargs) or {},
    )

    result = settings_service.test_llm_model("summary", "model-a", True)

    assert result["success"] is True
    assert captured["payload"]["max_tokens"] > 3072
    assert captured["timeout"] >= 120
    assert captured["budget"] > captured["timeout"]


def test_settings_payload_exposes_endpoints_and_drops_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 这份名单是扫 os.environ 得到的，而 get_settings() 会把 .env.local 灌进
    # os.environ：本机配了真实 Key 的机器上会多出几项，严格等值断言就会红。
    # 先把 env 文件落进来（load_environment 是幂等的），再把所有 *_API_KEY 清空，
    # 只留本用例注入的那一个，断言才是「与机器环境无关」的。
    load_environment()
    for name in [key for key in os.environ if LLM_API_KEY_ENV_PATTERN.fullmatch(key)]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "deepseek-key")

    def section_row(section: str, value: Any, version: int) -> dict[str, Any]:
        return {
            "section": section,
            "value": value,
            "version": version,
            "updated_at": None,
            "updated_by_user_id": None,
            "updated_by_display_name": None,
        }

    app_config = SimpleNamespace(
        fetch_settings=lambda: [
            section_row("llm_models", _llm_value(), 3),
            section_row("llm_endpoints", _llm_endpoints_value(), 2),
        ],
    )
    _patch_settings_adapter(
        monkeypatch,
        SimpleNamespace(app_config=app_config),
    )

    payload = settings_service.get_settings_payload()

    assert "llm_api_base_url" not in payload["environment"]
    endpoints = payload["endpoints"]
    assert endpoints["default"] == "openrouter"
    assert "openrouter.ai" in endpoints["allowed_hosts"]
    items = {item["key"]: item for item in endpoints["items"]}
    assert set(items["openrouter"]) == {
        "key",
        "label",
        "base_url",
        "api_key_env",
        "api_style",
        "temperature_override",
        "default_model",
        "host_allowed",
    }
    assert items["openrouter"]["default_model"] == "default-model"
    assert items["deepseek"]["default_model"] == "deepseek-default"
    # 配置状态统一由 configured_api_key_envs 名单表达，逐项字段不再各存一份
    assert endpoints["configured_api_key_envs"] == ["LLM_DEEPSEEK_API_KEY"]
    assert items["openrouter"]["host_allowed"] is True
    assert items["deepseek"]["host_allowed"] is True
    serialized = json.dumps(endpoints)
    assert "deepseek-key" not in serialized
    assert "prompt_optimizer" in {
        item["key"] for item in payload["steps"]
    }
    assert next(
        item for item in payload["steps"] if item["key"] == "prompt_optimizer"
    )["display_name"] == "提示词优化"


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


def test_chinadaily_account_parse_normalizes_channel_url() -> None:
    parsed = settings_service.parse_account(
        "chinadaily",
        "//cn.chinadaily.com.cn/6597728fa310af3247ffaeae",
    )

    assert parsed == {
        "source": "chinadaily",
        "normalized_identifier": "https://cn.chinadaily.com.cn/6597728fa310af3247ffaeae",
        "original_input": "//cn.chinadaily.com.cn/6597728fa310af3247ffaeae",
        "profile_url": "https://cn.chinadaily.com.cn/6597728fa310af3247ffaeae",
    }


def test_chinadaily_account_parse_rejects_article_and_foreign_urls() -> None:
    with pytest.raises(ValueError):
        settings_service.parse_account(
            "chinadaily",
            "https://cn.chinadaily.com.cn/a/202609/24/WS6ab4bacde4b09a165c78c73e.html",
        )
    with pytest.raises(ValueError):
        settings_service.parse_account("chinadaily", "https://example.com/6597728fa310af3247ffaeae")


def test_21jingji_account_parse_normalizes_channel_url() -> None:
    parsed = settings_service.parse_account(
        "21jingji",
        "//www.21jingji.com/channel/politics",
    )

    assert parsed == {
        "source": "21jingji",
        "normalized_identifier": "https://www.21jingji.com/channel/politics/",
        "original_input": "//www.21jingji.com/channel/politics",
        "profile_url": "https://www.21jingji.com/channel/politics/",
    }


def test_21jingji_account_parse_rejects_article_and_foreign_urls() -> None:
    with pytest.raises(ValueError):
        settings_service.parse_account(
            "21jingji",
            "https://www.21jingji.com/article/20261020/herald/a114daa8.html",
        )
    with pytest.raises(ValueError):
        settings_service.parse_account(
            "21jingji",
            "https://example.com/channel/politics/",
        )


def test_xinjinghao_account_parse_normalizes_bare_id_and_url() -> None:
    for raw in (
        "296",
        "https://m.bjnews.com.cn/mp/296",
        "https://m.bjnews.com.cn/mp/296?from=list",
    ):
        parsed = settings_service.parse_account("xinjinghao", raw)

        assert parsed["source"] == "xinjinghao"
        assert parsed["normalized_identifier"] == "296"
        assert parsed["profile_url"] == "https://m.bjnews.com.cn/mp/296"
        assert parsed["original_input"] == raw


def test_xinjinghao_account_parse_rejects_article_and_foreign_urls() -> None:
    for raw in (
        "https://m.bjnews.com.cn/detail/1793885788019604.html",
        "https://www.bjnews.com.cn/mp/296",
        "https://example.com/mp/296",
    ):
        with pytest.raises(ValueError):
            settings_service.parse_account("xinjinghao", raw)


def test_qianlong_account_parse_normalizes_channel_url() -> None:
    parsed = settings_service.parse_account("qianlong", "https://edu.qianlong.com/")

    assert parsed == {
        "source": "qianlong",
        "normalized_identifier": "https://edu.qianlong.com",
        "original_input": "https://edu.qianlong.com/",
        "profile_url": "https://edu.qianlong.com",
    }


def test_qianlong_account_parse_rejects_foreign_hosts() -> None:
    with pytest.raises(ValueError):
        settings_service.parse_account("qianlong", "https://example.com/")
    with pytest.raises(ValueError):
        settings_service.parse_account("qianlong", "https://qianlong.com.evil.com/")


def _stub_probes(
    monkeypatch: pytest.MonkeyPatch,
    records: list[tuple[str, str, bool]],
    *,
    succeed: bool = True,
) -> None:
    """记录保存路径实际探针的组合（接入点 key, 模型, reasoning）。

    步骤组合测试与接入点默认模型测试已统一走 _probe_model（传入定义），
    一次打桩即可观察两类探针；只用于组合/去重断言，不断言请求细节。"""

    def fake_probe(*, model, reasoning, endpoint_definition, operation, step=None):
        records.append((endpoint_definition.key, model, reasoning))
        return {
            "success": succeed,
            "elapsed_ms": 1,
            "error": None if succeed else f"{endpoint_definition.key}:{model}:probe failed",
        }

    monkeypatch.setattr(settings_service, "_probe_model", fake_probe)


def _stub_llm_http(monkeypatch: pytest.MonkeyPatch, records: list[dict[str, Any]]) -> None:
    """只替换 HTTP 发送层 post_chat_completion：记录实际请求的
    URL / Authorization / 模型。探针内部的定义解析、Key 读取、
    接入点解析全部走真实代码——打桩位置太高会把「用错配置」这类
    问题藏起来（本次补丁的教训）。"""

    def fake_post(url, **kwargs):
        records.append(
            {
                "url": url,
                "authorization": kwargs["headers"].get("Authorization"),
                "model": kwargs["payload"]["model"],
                "operation": kwargs.get("operation"),
            }
        )
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(settings_service, "post_chat_completion", fake_post)


def _glm_item() -> dict[str, Any]:
    return {
        "key": "glm",
        "label": "GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "LLM_GLM_API_KEY",
        "api_style": "thinking",
        "temperature_override": None,
        "default_model": "glm-4.6",
    }


def test_m16_changed_model_is_tested_server_side_before_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=calls,
        ),
    )
    records: list[tuple[str, str, bool]] = []
    _stub_probes(monkeypatch, records, succeed=False)
    after = _llm_value(summary="new-summary-model")
    # 模型名只在某个服务商处有效：填了模型就必须固定接入点
    after["steps"]["summary"]["endpoint"] = "openrouter"

    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin
    response = TestClient(app).put(
        "/api/admin/settings/llm_models",
        json={"value": after, "expected_version": 4},
    )

    assert response.status_code == 422
    assert "摘要生成模型测试失败" in response.json()["detail"]
    assert calls == []


def test_m16_model_without_endpoint_is_rejected_before_any_test(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    after = _llm_value(summary="orphan-model")
    saves: list[dict[str, Any]] = []
    records: list[tuple[str, str, bool]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    with pytest.raises(ValueError, match="摘要生成"):
        settings_service.update_setting(
            "llm_models",
            value=after,
            expected_version=4,
            actor=_admin(),
        )

    assert saves == []
    assert records == []


def test_m16_reasoning_only_change_is_tested_before_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _llm_value()
    after = _llm_value()
    after["steps"]["summary"]["reasoning"] = True
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": before,
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_models",
        value=after,
        expected_version=4,
        actor=_admin(),
    )

    assert records == [("openrouter", "default-model", True)]
    assert len(saves) == 1


def test_models_save_endpoint_without_model_is_legal_and_uses_endpoint_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """接入点 + 空模型：用该接入点的 default_model，是合法组合并实测该组合。"""

    after = _llm_value()
    after["steps"]["duplicate_review"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "deepseek",
    }
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_models",
        value=after,
        expected_version=4,
        actor=_admin(),
    )

    assert records == [("deepseek", "deepseek-default", True)]
    assert len(saves) == 1


def test_models_save_switching_step_endpoint_tests_resolved_combination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    after = _llm_value()
    after["steps"]["source"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "deepseek",
    }
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_models",
        value=after,
        expected_version=4,
        actor=_admin(),
    )

    assert records == [("deepseek", "deepseek-default", True)]
    assert len(saves) == 1


def test_endpoint_save_switching_default_tests_all_following_combinations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """切换默认接入点：跟随默认步骤的原始字段一个都没变，
    但解析出的「接入点 + 模型 + reasoning」全变了，必须实测（相同组合只测一次）。"""

    endpoints = _llm_endpoints_value()
    endpoints["default"] = "deepseek"
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    # 八个步骤都跟随默认；组合去重后只按 reasoning 分两批实测；
    # 接入点本身没有新增、default_model 没改 → 无接入点级探针
    assert records == [
        ("deepseek", "deepseek-default", False),
        ("deepseek", "deepseek-default", True),
    ]
    assert len(saves) == 1


def test_endpoint_default_model_change_tests_using_step_and_endpoint_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = _llm_endpoints_value()
    endpoints["items"][1]["default_model"] = "deepseek-chat-v4"
    models = _llm_value()
    models["steps"]["duplicate_review"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "deepseek",
    }
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": models, "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    # 用它的步骤实测新默认组合；接入点自身（reasoning 关）另测一次
    assert records == [
        ("deepseek", "deepseek-chat-v4", True),
        ("deepseek", "deepseek-chat-v4", False),
    ]
    assert len(saves) == 1


def test_endpoint_save_tests_unused_new_and_default_changed_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = _llm_endpoints_value()
    # deepseek 没有任何步骤引用：改它的默认模型只触发接入点级探针
    endpoints["items"][1]["default_model"] = "deepseek-chat-v4"
    endpoints["items"].append({**_glm_item(), "default_model": "glm-4"})
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    # 所有步骤都跟随 openrouter，解析组合未变 → 无步骤测试
    assert sorted(records) == [
        ("deepseek", "deepseek-chat-v4", False),
        ("glm", "glm-4", False),
    ]
    assert len(saves) == 1


def test_unchanged_configuration_sends_no_test_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # llm_models：提交与当前一致的值 → 不发任何测试
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_models",
        value=_llm_value(),
        expected_version=4,
        actor=_admin(),
    )

    # llm_endpoints：只改显示名，解析组合与连接信息都没变 → 同样不发测试
    endpoints = _llm_endpoints_value()
    endpoints["items"][0]["label"] = "OpenRouter 改名"
    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    assert records == []
    assert len(saves) == 2


def test_endpoint_connection_change_tests_steps_even_with_same_combination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = _llm_endpoints_value()
    # 换了 Key 环境变量：解析组合没变，但连接信息变了 → 跟随步骤要重测
    endpoints["items"][0]["api_key_env"] = "LLM_OPENROUTER_API_KEY"
    records: list[tuple[str, str, bool]] = []
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records)

    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    assert records == [
        ("openrouter", "default-model", False),
        ("openrouter", "default-model", True),
    ]
    assert len(saves) == 1


def test_failed_endpoint_default_model_test_rejects_save_naming_endpoint_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = _llm_endpoints_value()
    endpoints["items"][1]["default_model"] = "broken-model"
    saves: list[dict[str, Any]] = []
    records: list[tuple[str, str, bool]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records, succeed=False)

    with pytest.raises(ValueError, match="DeepSeek") as excinfo:
        settings_service.update_setting(
            "llm_endpoints",
            value=endpoints,
            expected_version=4,
            actor=_admin(),
        )

    assert "broken-model" in str(excinfo.value)
    # 整次保存被拒绝：数据库不变
    assert saves == []


def test_failed_step_test_rejects_models_save_and_keeps_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    after = _llm_value()
    after["steps"]["summary"] = {
        "model": "new-summary-model",
        "reasoning": False,
        "endpoint": "openrouter",
    }
    saves: list[dict[str, Any]] = []
    records: list[tuple[str, str, bool]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )
    _stub_probes(monkeypatch, records, succeed=False)

    with pytest.raises(ValueError, match="摘要生成模型测试失败"):
        settings_service.update_setting(
            "llm_models",
            value=after,
            expected_version=4,
            actor=_admin(),
        )

    assert saves == []


# ---------- 保存探针使用待保存定义（只替换 HTTP 发送层） ----------


def test_save_probes_new_endpoint_with_pending_definition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新增接入点：探针发往**新**接入点的地址、带**新** Key、模型是它的默认模型。"""

    endpoints = _llm_endpoints_value()
    endpoints["items"].append(_glm_item())
    saves: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_GLM_API_KEY", "glm-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    http: list[dict[str, Any]] = []
    _stub_llm_http(monkeypatch, http)

    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    assert len(saves) == 1
    # 只有接入点级探针（glm 是新增）：发往新地址、新 Key、默认模型
    assert len(http) == 1
    assert http[0]["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    assert http[0]["authorization"] == "Bearer glm-key"
    assert http[0]["model"] == "glm-4.6"


def test_save_rejects_new_endpoint_with_missing_key_naming_env_and_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新增接入点但 Key 环境变量未配置：保存被拒、数据库不变、零请求，
    报错写明环境变量名和处理步骤（先在服务器 .env.local 配好，再重新保存）。"""

    endpoints = _llm_endpoints_value()
    endpoints["items"].append(_glm_item())
    saves: list[dict[str, Any]] = []
    monkeypatch.delenv("LLM_GLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    http: list[dict[str, Any]] = []

    def must_not_post(url, **kwargs):
        http.append({"url": url})
        raise AssertionError("Key 未配置时不得发出任何请求")

    monkeypatch.setattr(settings_service, "post_chat_completion", must_not_post)

    with pytest.raises(ValueError) as excinfo:
        settings_service.update_setting(
            "llm_endpoints",
            value=endpoints,
            expected_version=4,
            actor=_admin(),
        )

    message = str(excinfo.value)
    assert "LLM_GLM_API_KEY" in message
    assert ".env.local" in message
    assert "再重新保存" in message
    assert "重新加载环境变量" not in message
    assert saves == []
    assert http == []


def test_save_retests_steps_with_new_key_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """改某接入点的 api_key_env：跟随步骤重测时带的是**新** Key。"""

    endpoints = _llm_endpoints_value()
    endpoints["items"][0]["api_key_env"] = "LLM_OPENROUTER_V2_API_KEY"
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_OPENROUTER_V2_API_KEY", "new-openrouter-key")
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    http: list[dict[str, Any]] = []
    _stub_llm_http(monkeypatch, http)

    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    assert len(saves) == 1
    # 旧 Key 环境变量已删：请求带得出 Authorization 只能是 新 Key
    assert http, "连接信息变化必须触发步骤重测"
    for record in http:
        assert record["authorization"] == "Bearer new-openrouter-key"
        assert record["url"] == "https://openrouter.ai/api/v1/chat/completions"


def test_save_retests_steps_with_new_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """改某接入点的 base_url：重测请求发往**新**地址。"""

    endpoints = _llm_endpoints_value()
    endpoints["items"][0]["base_url"] = "https://openrouter.ai/api/v2"
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": _llm_endpoints_value()},
            saves=saves,
        ),
    )
    http: list[dict[str, Any]] = []
    _stub_llm_http(monkeypatch, http)

    settings_service.update_setting(
        "llm_endpoints",
        value=endpoints,
        expected_version=4,
        actor=_admin(),
    )

    assert len(saves) == 1
    assert http, "连接信息变化必须触发步骤重测"
    for record in http:
        assert record["url"] == "https://openrouter.ai/api/v2/chat/completions"


def test_add_endpoint_via_http_api_returns_200_and_is_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通过管理接口新增接入点：PUT 200，探针实测新接入点，GET 立即可见。"""

    endpoints = _llm_endpoints_value()
    endpoints["items"].append(_glm_item())
    store: dict[str, dict[str, Any]] = {
        "llm_models": {"value": _llm_value(), "version": 4},
        "llm_endpoints": {"value": _llm_endpoints_value(), "version": 3},
    }

    class _Store:
        def fetch_settings(self) -> list[dict[str, Any]]:
            return [
                {
                    "section": section,
                    "value": item["value"],
                    "version": item["version"],
                    "updated_at": None,
                    "updated_by_user_id": None,
                    "updated_by_display_name": None,
                }
                for section, item in store.items()
            ]

        def fetch_setting(self, section: str) -> dict[str, Any] | None:
            item = store.get(section)
            return None if item is None else {"section": section, **item}

        def update_app_setting_as_user(
            self,
            *,
            section: str,
            value: Any,
            expected_version: int,
            actor_user_id: str,
        ) -> dict[str, Any]:
            item = store[section]
            if item["version"] != expected_version:
                from src.adapters.db_postgres_app_config import (
                    ConfigVersionConflictError,
                )

                raise ConfigVersionConflictError("配置版本冲突")
            item["value"] = value
            item["version"] += 1
            return {"section": section, "value": value, "version": item["version"]}

    monkeypatch.setattr(
        settings_service, "get_adapter", lambda: SimpleNamespace(app_config=_Store())
    )
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_GLM_API_KEY", "glm-key")
    http: list[dict[str, Any]] = []
    _stub_llm_http(monkeypatch, http)
    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin
    client = TestClient(app)

    put = client.put(
        "/api/admin/settings/llm_endpoints",
        json={"value": endpoints, "expected_version": 3},
    )

    assert put.status_code == 200
    # 探针发往新接入点（新增接入点曾经因探针查库直接保存失败）
    assert any(
        record["url"] == "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        and record["authorization"] == "Bearer glm-key"
        for record in http
    )

    fetched = client.get("/api/admin/settings")

    assert fetched.status_code == 200
    keys = [item["key"] for item in fetched.json()["endpoints"]["items"]]
    assert keys == ["openrouter", "deepseek", "glm"]


def test_llm_models_save_rejects_unknown_endpoint_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    after = _llm_value()
    after["steps"]["duplicate_review"] = {
        "model": "duplicate-model",
        "reasoning": True,
        "endpoint": "ghost",
    }
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {
                "llm_models": _llm_value(),
                "llm_endpoints": _llm_endpoints_value(),
            },
            saves=saves,
        ),
    )

    with pytest.raises(ValueError, match="ghost"):
        settings_service.update_setting(
            "llm_models",
            value=after,
            expected_version=4,
            actor=_admin(),
        )

    assert saves == []


def test_endpoint_save_rejects_deleting_referenced_or_default_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = _llm_endpoints_value()
    models = _llm_value()
    models["steps"]["duplicate_review"] = {
        "model": "duplicate-model",
        "reasoning": True,
        "endpoint": "deepseek",
    }
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": models, "llm_endpoints": endpoints},
            saves=saves,
        ),
    )

    # 删除被步骤引用的接入点
    dropped_deepseek = {
        "default": "openrouter",
        "items": [item for item in endpoints["items"] if item["key"] != "deepseek"],
    }
    with pytest.raises(ValueError, match="查重复核"):
        settings_service.update_setting(
            "llm_endpoints",
            value=dropped_deepseek,
            expected_version=4,
            actor=_admin(),
        )

    # 删除默认接入点
    dropped_openrouter = {
        "default": "deepseek",
        "items": [
            item for item in endpoints["items"] if item["key"] != "openrouter"
        ],
    }
    with pytest.raises(ValueError, match="默认接入点"):
        settings_service.update_setting(
            "llm_endpoints",
            value=dropped_openrouter,
            expected_version=4,
            actor=_admin(),
        )

    assert saves == []


def test_endpoint_save_rejects_non_whitelisted_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoints = _llm_endpoints_value()
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": endpoints},
        ),
    )

    endpoints = _llm_endpoints_value()
    endpoints["items"][1]["base_url"] = "https://evil.example.com/v1"

    with pytest.raises(ValueError) as excinfo:
        settings_service.update_setting(
            "llm_endpoints",
            value=endpoints,
            expected_version=4,
            actor=_admin(),
        )

    # 保存 llm_endpoints 仍要求全部接入点在白名单内；报错写明域名与追加语义
    message = str(excinfo.value)
    assert "evil.example.com" in message
    assert "允许列表内" in message
    assert "LLM_ALLOWED_HOSTS" in message
    assert ".env.local" in message
    assert "追加" in message


def test_update_setting_normalizes_wordlist_sections_before_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter({"education_keywords": ["教育"]}, saves=saves),
    )

    settings_service.update_setting(
        "education_keywords",
        value=[" 教育 ", "教育", "", "学校"],
        expected_version=4,
        actor=_admin(),
    )

    assert saves[0]["section"] == "education_keywords"
    assert saves[0]["value"] == ["教育", "学校"]


def test_update_setting_normalizes_score_exclusion_keywords(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter({"score_exclusion_keywords": []}, saves=saves),
    )

    settings_service.update_setting(
        "score_exclusion_keywords",
        value=[" 考试 ", "培训"],
        expected_version=4,
        actor=_admin(),
    )

    assert saves[0]["section"] == "score_exclusion_keywords"
    assert saves[0]["value"] == ["考试", "培训"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (["考试", " 考试 "], "关键词重复"),
        ([1], "必须是字符串"),
        ("考试", "必须是有序数组"),
    ],
)
def test_invalid_score_exclusion_keywords_are_rejected_over_admin_api(
    monkeypatch: pytest.MonkeyPatch,
    value: Any,
    message: str,
) -> None:
    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin

    response = TestClient(app).put(
        "/api/admin/settings/score_exclusion_keywords",
        json={"value": value, "expected_version": 1},
    )

    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_empty_wordlist_is_rejected_over_admin_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin

    response = TestClient(app).put(
        "/api/admin/settings/beijing_keywords",
        json={"value": [], "expected_version": 1},
    )

    assert response.status_code == 422
    assert "规范化后不能为空" in response.json()["detail"]


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([{"keyword": "高考", "bonus": 10}, {"keyword": "高考", "bonus": 20}], "关键词重复"),
        ([{"keyword": "高考", "bonus": 1000}], "-100 到 100"),
        ([{"keyword": "高考", "bonus": True}], "必须是整数"),
    ],
)
def test_invalid_bonus_rules_are_rejected_over_admin_api(
    monkeypatch: pytest.MonkeyPatch,
    value: Any,
    message: str,
) -> None:
    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin

    response = TestClient(app).put(
        "/api/admin/settings/score_keyword_bonuses",
        json={"value": value, "expected_version": 1},
    )

    assert response.status_code == 422
    assert message in response.json()["detail"]


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


def _clear_legacy_model_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_service, "load_environment", lambda: None)
    for key in (
        "CRAWL_SOURCES",
        "LLM_MODEL",
        "LLM_SUMMARY_MODEL",
        "LLM_SOURCE_MODEL",
        "LLM_SENTIMENT_MODEL",
        "LLM_SCORING_MODEL",
        "LLM_EXTERNAL_FILTER_MODEL",
        "LLM_BEIJING_GATE_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_f7_legacy_source_and_sentiment_models_follow_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_legacy_model_environment(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "default-model")
    monkeypatch.setenv("LLM_SUMMARY_MODEL", "summary-model")

    value = settings_service.preview_legacy_import(root=tmp_path)["sections"][
        "llm_models"
    ]

    assert value["steps"]["summary"]["model"] == "summary-model"
    assert value["steps"]["source"]["model"] == "summary-model"
    assert value["steps"]["sentiment"]["model"] == "summary-model"


def test_f7_legacy_filter_gate_and_duplicate_models_follow_scoring(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _clear_legacy_model_environment(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "default-model")
    monkeypatch.setenv("LLM_SCORING_MODEL", "scoring-model")

    value = settings_service.preview_legacy_import(root=tmp_path)["sections"][
        "llm_models"
    ]

    assert value["steps"]["scoring"]["model"] == "scoring-model"
    assert value["steps"]["external_filter"]["model"] == "scoring-model"
    assert value["steps"]["beijing_gate"]["model"] == "scoring-model"
    assert value["steps"]["duplicate_review"]["model"] == "scoring-model"


def test_legacy_import_includes_prompt_optimizer_step_with_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_legacy_model_environment(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "same-model")

    value = validate_llm_models(
        settings_service._legacy_models(
            default_endpoint="openrouter",
            default_model=settings_service.LLM_FALLBACK_DEFAULT_MODEL,
        )
    )

    assert value["steps"]["prompt_optimizer"] == {
        "model": None,
        "reasoning": True,
        "endpoint": None,
    }


def test_f7_legacy_models_equal_to_endpoint_default_are_stored_as_null(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """环境变量全空：所有步骤解析值等于迁移种子接入点的 default_model
    （LLM_FALLBACK_DEFAULT_MODEL），写双 null 跟随默认接入点。"""

    _clear_legacy_model_environment(monkeypatch)

    value = settings_service.preview_legacy_import(root=tmp_path)["sections"][
        "llm_models"
    ]

    assert all(item["model"] is None for item in value["steps"].values())
    assert all(item["endpoint"] is None for item in value["steps"].values())


def test_f7_legacy_models_differing_from_endpoint_default_bind_default_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """LLM_MODEL 是别的值：等于它的步骤必须显式绑定默认接入点 key + 该模型，
    否则切换默认接入点后解析结果会变（旧语义里它们解析为 LLM_MODEL）。"""

    _clear_legacy_model_environment(monkeypatch)
    monkeypatch.setenv("LLM_MODEL", "same-model")
    for key in (
        "LLM_SUMMARY_MODEL",
        "LLM_SOURCE_MODEL",
        "LLM_SENTIMENT_MODEL",
        "LLM_SCORING_MODEL",
        "LLM_EXTERNAL_FILTER_MODEL",
        "LLM_BEIJING_GATE_MODEL",
    ):
        monkeypatch.setenv(key, "same-model")

    value = settings_service.preview_legacy_import(root=tmp_path)["sections"][
        "llm_models"
    ]

    # prompt_optimizer 由校验层补默认值（双 null），不在旧环境变量的映射范围
    for step in ("summary", "source", "sentiment", "scoring", "external_filter",
                 "beijing_gate", "duplicate_review"):
        item = value["steps"][step]
        assert item["model"] == "same-model", step
        assert item["endpoint"] == "openrouter", step


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


class _AccountConfigFake:
    def __init__(self, accounts: list[dict[str, Any]]) -> None:
        self.accounts = {str(item["id"]): dict(item) for item in accounts}
        self.article_names: dict[tuple[str, str], str] = {}
        self.article_name_calls: list[tuple[str, str]] = []

    def fetch_account(self, account_id: str) -> dict[str, Any] | None:
        account = self.accounts.get(account_id)
        return dict(account) if account else None

    def fetch_accounts(self, source: str) -> list[dict[str, Any]]:
        return [
            dict(item)
            for item in self.accounts.values()
            if item["source"] == source
        ]

    def fetch_latest_account_article_source(
        self,
        *,
        source: str,
        normalized_identifier: str,
    ) -> str | None:
        self.article_name_calls.append((source, normalized_identifier))
        return self.article_names.get((source, normalized_identifier))

    def record_crawl_account_name_success(
        self,
        *,
        account_id: str,
        display_name: str,
    ) -> dict[str, Any]:
        account = self.accounts[account_id]
        account["display_name"] = display_name
        account["display_name_synced_at"] = "now"
        account["display_name_error"] = None
        return dict(account)

    def record_crawl_account_name_failure(
        self,
        *,
        account_id: str,
        error: str,
    ) -> dict[str, Any]:
        account = self.accounts[account_id]
        account["display_name_error"] = error
        return dict(account)

    def create_crawl_account_as_user(self, **kwargs: Any) -> dict[str, Any]:
        account = {
            "id": "created-id",
            **kwargs,
            "display_name_synced_at": None,
            "display_name_error": None,
        }
        account.pop("actor_user_id")
        self.accounts["created-id"] = account
        return dict(account)


def _account_row(
    account_id: str,
    *,
    source: str = "tencent",
    display_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": account_id,
        "source": source,
        "normalized_identifier": f"identifier-{account_id}",
        "original_input": f"identifier-{account_id}",
        "profile_url": f"https://example.test/{account_id}",
        "display_name": display_name,
        "display_name_synced_at": None,
        "display_name_error": None,
        "updated_by_user_id": "original-editor",
    }


def test_refresh_names_covers_resolved_unchanged_failed_and_m2_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config = _AccountConfigFake(
        [
            _account_row("resolved"),
            _account_row("unchanged", display_name="原名"),
            _account_row("failed", display_name="保留名"),
            _account_row("skipped"),
        ]
    )
    adapter = SimpleNamespace(app_config=app_config)
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)
    clock = iter([0.0, 1.0, 2.0, 3.0, 31.0])
    monkeypatch.setattr(settings_service.time, "monotonic", lambda: next(clock))

    def resolve(source: str, **kwargs: Any) -> str:
        identifier = kwargs["normalized_identifier"]
        if identifier == "identifier-resolved":
            return "新名称"
        if identifier == "identifier-unchanged":
            return "原名"
        raise AccountNameUnavailable("主页返回 404")

    monkeypatch.setattr(settings_service, "resolve_account_name", resolve)

    items = settings_service.refresh_account_names(
        ["resolved", "unchanged", "failed", "skipped"]
    )

    assert [item["status"] for item in items] == [
        "resolved",
        "unchanged",
        "failed",
        "skipped",
    ]
    assert items[0]["account"]["display_name"] == "新名称"
    assert items[1]["account"]["display_name_synced_at"] == "now"
    assert items[2]["account"] is None
    assert items[2]["error"] == "主页返回 404"
    assert app_config.accounts["failed"]["display_name"] == "保留名"
    assert items[3]["account"] is None
    assert "30 秒" in items[3]["error"]


def test_toutiao_uses_database_before_http_and_tencent_falls_back_after_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toutiao = _account_row("tt", source="toutiao")
    tencent = _account_row("tx", source="tencent")
    app_config = _AccountConfigFake([toutiao, tencent])
    app_config.article_names[("toutiao", "identifier-tt")] = "头条库内名"
    app_config.article_names[("tencent", "identifier-tx")] = "腾讯库内名"
    adapter = SimpleNamespace(app_config=app_config)
    calls: list[str] = []

    def unavailable(source: str, **kwargs: Any) -> str:
        calls.append(source)
        raise AccountNameUnavailable("HTTP 失败")

    monkeypatch.setattr(settings_service, "resolve_account_name", unavailable)

    assert settings_service._resolve_account_name_with_fallback(
        adapter, toutiao, timeout=8
    ) == "头条库内名"
    assert settings_service._resolve_account_name_with_fallback(
        adapter, tencent, timeout=8
    ) == "腾讯库内名"
    assert calls == ["tencent"]


@pytest.mark.parametrize("source", ["btime", "beijinghao", "xinjinghao"])
def test_http_account_sources_never_use_article_fallback(
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    account = _account_row("one", source=source)
    app_config = _AccountConfigFake([account])
    adapter = SimpleNamespace(app_config=app_config)
    monkeypatch.setattr(
        settings_service,
        "resolve_account_name",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AccountNameUnavailable("HTTP 失败")
        ),
    )

    with pytest.raises(AccountNameUnavailable, match="HTTP 失败"):
        settings_service._resolve_account_name_with_fallback(
            adapter,
            account,
            timeout=8,
        )

    assert app_config.article_name_calls == []


def test_m7_create_api_returns_201_without_blocking_on_name_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config = _AccountConfigFake([])
    adapter = SimpleNamespace(app_config=app_config)
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)

    def must_not_resolve(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("创建请求不应同步解析名称（头条解析要起无头浏览器）")

    monkeypatch.setattr(settings_service, "resolve_account_name", must_not_resolve)
    monkeypatch.setattr("src.console.app.warn_legacy_config", lambda: [])
    app = create_app()
    app.dependency_overrides[require_console_user] = _admin

    response = TestClient(app).post(
        "/api/admin/crawl-accounts",
        json={"source": "toutiao", "text": "token-1"},
    )

    # 201 即回、无名称无错误：前端随后对该条调 refresh-names 补解析
    assert response.status_code == 201
    assert response.json()["item"]["display_name"] is None
    assert response.json()["item"]["display_name_error"] is None
    assert app_config.article_name_calls == []


def test_refresh_names_gives_toutiao_browser_budget_and_http_sources_eight_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_config = _AccountConfigFake(
        [
            _account_row("tt", source="toutiao"),
            _account_row("tx", source="tencent"),
        ]
    )
    adapter = SimpleNamespace(app_config=app_config)
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)
    observed: list[tuple[str, float]] = []

    def resolve(source: str, **kwargs: Any) -> str:
        observed.append((source, float(kwargs["timeout"])))
        raise AccountNameUnavailable("HTTP 失败")

    monkeypatch.setattr(settings_service, "resolve_account_name", resolve)

    items = settings_service.refresh_account_names(["tt", "tx"])

    assert [item["status"] for item in items] == ["failed", "failed"]
    assert observed == [
        ("toutiao", PROFILE_NAME_TIMEOUT_SECONDS),
        ("tencent", MAX_ACCOUNT_TIMEOUT_SECONDS),
    ]


def test_toutiao_refresh_keeps_existing_name_without_browser_roundtrip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toutiao = _account_row("tt", source="toutiao", display_name="保留名")
    app_config = _AccountConfigFake([toutiao])
    adapter = SimpleNamespace(app_config=app_config)
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)

    def must_not_resolve(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("已有名称的头条账号不应再走网页解析")

    monkeypatch.setattr(settings_service, "resolve_account_name", must_not_resolve)

    items = settings_service.refresh_account_names(["tt"])

    assert items[0]["status"] == "unchanged"
    assert items[0]["account"]["display_name"] == "保留名"
    # 不对已有名的账号做库内反查之外的任何解析
    assert app_config.article_name_calls == [("toutiao", "identifier-tt")]


# ---------- 已配置 Key 名单（configured_api_key_envs）与白名单失效场景 ----------


def _patch_allowed_hosts(monkeypatch: pytest.MonkeyPatch, hosts: tuple[str, ...]) -> None:
    """把两处 get_settings 都指到同一份自定义白名单：settings_service 直接导入，
    business_config 在函数内按模块属性取。"""

    settings = replace(get_settings(), llm_allowed_hosts=hosts)
    monkeypatch.setattr(settings_service, "get_settings", lambda: settings)
    monkeypatch.setattr("src.config.get_settings", lambda: settings)


def _endpoints_section_adapter(
    monkeypatch: pytest.MonkeyPatch,
    endpoints_value: dict[str, Any],
    models_value: dict[str, Any] | None = None,
) -> SimpleNamespace:
    sections: dict[str, Any] = {"llm_endpoints": endpoints_value}
    if models_value is not None:
        sections["llm_models"] = models_value
    adapter = _settings_section_adapter(sections)

    def fetch_settings() -> list[dict[str, Any]]:
        return [
            {"section": section, "value": value, "version": 4,
             "updated_at": None, "updated_by_user_id": None,
             "updated_by_display_name": None}
            for section, value in sections.items()
        ]

    adapter.app_config.fetch_settings = fetch_settings
    return adapter


def test_configured_api_key_envs_filters_by_format_and_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """名单只含「已设置且名字合法」的变量：未设置、空串、名字不合法的都不进。"""

    monkeypatch.setenv("ZZ_GOOD_TEST_API_KEY", "some-key")
    monkeypatch.setenv("AA_GOOD_TEST_API_KEY", "other-key")
    monkeypatch.setenv("EMPTY_TEST_API_KEY", "")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("LLM_API_KEY_V2", "bad-suffix")
    monkeypatch.delenv("UNSET_TEST_API_KEY", raising=False)
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, _llm_endpoints_value()),
    )

    payload = settings_service.get_settings_payload()
    configured = payload["endpoints"]["configured_api_key_envs"]

    assert "ZZ_GOOD_TEST_API_KEY" in configured
    assert "AA_GOOD_TEST_API_KEY" in configured
    assert "EMPTY_TEST_API_KEY" not in configured, "值为空串的变量不算已配置"
    assert "PATH" not in configured, "名字不符合 LLM_API_KEY_ENV_PATTERN 的变量不进名单"
    assert "LLM_API_KEY_V2" not in configured
    assert "UNSET_TEST_API_KEY" not in configured
    assert configured == sorted(configured), "名单必须按字母排序"


def test_settings_payload_never_contains_key_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负载里只给变量名：任何 Key 的值（哪怕一部分）都不得出现在序列化结果里。"""

    unique_value = "sk-super-secret-probe-value-9f8e7d6c"
    monkeypatch.setenv("SECRET_PROBE_TEST_API_KEY", unique_value)
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, _llm_endpoints_value()),
    )

    payload = settings_service.get_settings_payload()
    serialized = json.dumps(payload, ensure_ascii=False)

    assert unique_value not in serialized
    assert unique_value[:20] not in serialized
    assert "SECRET_PROBE_TEST_API_KEY" in payload["endpoints"]["configured_api_key_envs"]


def test_configured_list_matches_save_time_missing_key_judgment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """名单与保存实测共用同一条判定：同一个空串变量，既不在名单里，
    保存时也被判为缺 Key 拒绝。"""

    monkeypatch.setenv("EMPTY_MATCH_TEST_API_KEY", "")
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(
            monkeypatch, _llm_endpoints_value(), models_value=_llm_value()
        ),
    )
    payload = settings_service.get_settings_payload()
    assert "EMPTY_MATCH_TEST_API_KEY" not in payload["endpoints"]["configured_api_key_envs"]

    endpoints = _llm_endpoints_value()
    endpoints["items"].append(
        {
            "key": "emptyvar",
            "label": "EmptyVar",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key_env": "EMPTY_MATCH_TEST_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "m",
        }
    )
    monkeypatch.setattr(settings_service, "post_chat_completion", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("不应发请求")))

    with pytest.raises(ValueError) as excinfo:
        settings_service.update_setting(
            "llm_endpoints",
            value=endpoints,
            expected_version=4,
            actor=_admin(),
        )
    assert "EMPTY_MATCH_TEST_API_KEY" in str(excinfo.value)


def test_settings_payload_has_configured_list_without_endpoints_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llm_endpoints 分区缺失（未 import-settings）时负载也带这个字段。"""

    def section_row(section: str, value: Any, version: int) -> dict[str, Any]:
        return {
            "section": section,
            "value": value,
            "version": version,
            "updated_at": None,
            "updated_by_user_id": None,
            "updated_by_display_name": None,
        }

    monkeypatch.setenv("ORPHAN_TEST_API_KEY", "v")
    app_config = SimpleNamespace(
        fetch_settings=lambda: [section_row("llm_models", _llm_value(), 3)],
    )
    _patch_settings_adapter(
        monkeypatch,
        SimpleNamespace(app_config=app_config),
    )

    endpoints = settings_service.get_settings_payload()["endpoints"]

    assert endpoints["default"] is None
    assert endpoints["items"] == []
    assert "ORPHAN_TEST_API_KEY" in endpoints["configured_api_key_envs"]


def test_settings_payload_marks_blocked_endpoint_host_and_still_serves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1：库里有接入点指向白名单外域名时，设置页负载照常生成，
    该接入点带 host_allowed=False，其余接入点不受影响。"""

    _patch_allowed_hosts(monkeypatch, ("openrouter.ai", "api.deepseek.com"))
    endpoints = _llm_endpoints_value()
    endpoints["items"].append(
        {
            "key": "legacy",
            "label": "Legacy",
            "base_url": "https://evil.example.com/v1",
            "api_key_env": "LLM_LEGACY_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "legacy-model",
        }
    )
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, endpoints),
    )

    payload = settings_service.get_settings_payload()
    items = {item["key"]: item for item in payload["endpoints"]["items"]}

    assert items["legacy"]["host_allowed"] is False
    assert items["openrouter"]["host_allowed"] is True
    assert items["deepseek"]["host_allowed"] is True


def test_llm_models_save_moves_steps_off_blocked_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H2：库里有白名单外接入点时，保存 llm_models 把步骤从它身上改走能成功。"""

    _patch_allowed_hosts(monkeypatch, ("openrouter.ai", "api.deepseek.com"))
    endpoints = _llm_endpoints_value()
    endpoints["items"].append(
        {
            "key": "legacy",
            "label": "Legacy",
            "base_url": "https://evil.example.com/v1",
            "api_key_env": "LLM_LEGACY_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "legacy-model",
        }
    )
    models = _llm_value()
    models["steps"]["scoring"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "legacy",
    }
    models["steps"]["beijing_gate"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "legacy",
    }
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": models, "llm_endpoints": endpoints},
            saves=saves,
        ),
    )
    records: list[tuple[str, str, bool]] = []
    _stub_probes(monkeypatch, records)

    after = _llm_value()
    after["steps"]["scoring"] = {"model": None, "reasoning": True, "endpoint": None}
    after["steps"]["beijing_gate"] = {
        "model": None,
        "reasoning": True,
        "endpoint": None,
    }
    settings_service.update_setting(
        "llm_models",
        value=after,
        expected_version=4,
        actor=_admin(),
    )

    assert len(saves) == 1
    # 被挪动的步骤重测组合解析到默认接入点（白名单内），不再碰 legacy
    tested_keys = {key for key, _model, _reasoning in records}
    assert "legacy" not in tested_keys


def test_llm_endpoints_save_allows_fixing_or_removing_blocked_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1：白名单外的接入点可以在同一次保存里改地址或删掉；
    原样保留则被拒。"""

    _patch_allowed_hosts(monkeypatch, ("openrouter.ai", "api.deepseek.com"))
    endpoints = _llm_endpoints_value()
    endpoints["items"].append(
        {
            "key": "legacy",
            "label": "Legacy",
            "base_url": "https://evil.example.com/v1",
            "api_key_env": "LLM_LEGACY_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "legacy-model",
        }
    )
    # 已保存 llm_models 没有任何步骤引用 legacy：删除不冲突
    saves: list[dict[str, Any]] = []
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": _llm_value(), "llm_endpoints": endpoints},
            saves=saves,
        ),
    )
    records: list[tuple[str, str, bool]] = []
    _stub_probes(monkeypatch, records)

    removed = _llm_endpoints_value()
    settings_service.update_setting(
        "llm_endpoints",
        value=removed,
        expected_version=4,
        actor=_admin(),
    )
    assert len(saves) == 1

    fixed = _llm_endpoints_value()
    fixed["items"].append(
        {
            "key": "legacy",
            "label": "Legacy",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "LLM_LEGACY_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "legacy-model",
        }
    )
    settings_service.update_setting(
        "llm_endpoints",
        value=fixed,
        expected_version=4,
        actor=_admin(),
    )
    assert len(saves) == 2

    kept = _llm_endpoints_value()
    kept["items"].append(
        {
            "key": "legacy",
            "label": "Legacy",
            "base_url": "https://evil.example.com/v1",
            "api_key_env": "LLM_LEGACY_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "legacy-model",
        }
    )
    with pytest.raises(ValueError, match="允许列表内"):
        settings_service.update_setting(
            "llm_endpoints",
            value=kept,
            expected_version=4,
            actor=_admin(),
        )


def test_test_button_never_requests_blocked_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H3：「测试」按钮对白名单外主机在发请求前拦下，报错写明域名与处理办法。"""

    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    _patch_allowed_hosts(monkeypatch, ("openrouter.ai", "api.deepseek.com"))
    endpoints = _llm_endpoints_value()
    endpoints["items"].append(
        {
            "key": "legacy",
            "label": "Legacy",
            "base_url": "https://evil.example.com/v1",
            "api_key_env": "LLM_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "legacy-model",
        }
    )
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, endpoints),
    )

    def must_not_post(url, **kwargs):
        raise AssertionError("白名单外主机不得发出任何请求")

    monkeypatch.setattr(settings_service, "post_chat_completion", must_not_post)

    result = settings_service.test_llm_model("summary", "model-a", False, endpoint="legacy")

    assert result["success"] is False
    assert "evil.example.com" in result["error"]
    assert "LLM_ALLOWED_HOSTS" in result["error"]
    assert ".env.local" in result["error"]


def test_models_save_step_probe_never_requests_blocked_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H3：保存 llm_models 时步骤实测对白名单外主机不发请求，保存被拒并写明域名。"""

    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    _patch_allowed_hosts(monkeypatch, ("openrouter.ai", "api.deepseek.com"))
    endpoints = _llm_endpoints_value()
    endpoints["items"].append(
        {
            "key": "legacy",
            "label": "Legacy",
            "base_url": "https://evil.example.com/v1",
            "api_key_env": "LLM_API_KEY",
            "api_style": "thinking",
            "temperature_override": None,
            "default_model": "legacy-model",
        }
    )
    models = _llm_value()
    models["steps"]["scoring"] = {
        "model": "old-model",
        "reasoning": True,
        "endpoint": "legacy",
    }
    _patch_settings_adapter(
        monkeypatch,
        _settings_section_adapter(
            {"llm_models": models, "llm_endpoints": endpoints},
        ),
    )

    def must_not_post(url, **kwargs):
        raise AssertionError("白名单外主机不得发出任何请求")

    monkeypatch.setattr(settings_service, "post_chat_completion", must_not_post)

    after = _llm_value()
    after["steps"]["scoring"] = {
        "model": "new-model",
        "reasoning": True,
        "endpoint": "legacy",
    }
    with pytest.raises(ValueError) as excinfo:
        settings_service.update_setting(
            "llm_models",
            value=after,
            expected_version=4,
            actor=_admin(),
        )

    message = str(excinfo.value)
    assert "相关性评分" in message
    assert "evil.example.com" in message


def test_beijing_gate_step_probe_carries_adapter_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1：京内判定步骤实测带的 response_format 与 build_response_format
    对该接入点给出的一致（OpenRouter json_schema / 其余 json_object 各一例）。"""

    from src.adapters import llm_beijing_gate
    from src.adapters.llm_endpoint import resolve_endpoint
    from src.business_config import resolve_llm_endpoints as _resolve

    captured: dict[str, Any] = {}
    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "deepseek-key")
    endpoints_value = _llm_endpoints_value()
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, endpoints_value),
    )

    def fake_post(url, **kwargs):
        captured["payload"] = kwargs["payload"]
        captured["model"] = kwargs.get("model")
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(settings_service, "post_chat_completion", fake_post)

    result = settings_service.test_llm_model("beijing_gate", "gate-model", False)
    assert result["success"] is True
    endpoint_items, _default = _resolve(endpoints_value, require_host_allowed=False)
    expected = llm_beijing_gate.build_response_format(
        resolve_endpoint(endpoint_items["openrouter"])
    )
    assert captured["payload"]["response_format"] == expected
    assert captured["payload"]["response_format"]["type"] == "json_schema"

    result = settings_service.test_llm_model(
        "beijing_gate", "gate-model", False, endpoint="deepseek"
    )
    assert result["success"] is True
    expected = llm_beijing_gate.build_response_format(
        resolve_endpoint(endpoint_items["deepseek"])
    )
    assert captured["payload"]["response_format"] == expected
    assert captured["payload"]["response_format"]["type"] == "json_object"


def test_step_probe_reuses_adapter_response_format_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2：探针复用 adapter 的构造逻辑——改掉 build_response_format 的返回值，
    探针请求随之变化，而不是拿一份写死的副本。"""

    from src.adapters import llm_beijing_gate

    captured: dict[str, Any] = {}
    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, _llm_endpoints_value()),
    )

    def fake_post(url, **kwargs):
        captured["payload"] = kwargs["payload"]
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(settings_service, "post_chat_completion", fake_post)
    sentinel = {"type": "json_object", "probe_sentinel": True}
    monkeypatch.setattr(llm_beijing_gate, "build_response_format", lambda endpoint: sentinel)

    result = settings_service.test_llm_model("beijing_gate", "gate-model", False)

    assert result["success"] is True
    assert captured["payload"]["response_format"] == sentinel


def test_step_probe_carries_step_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3：各步骤实测请求里的 temperature 与该步骤真实请求一致
    （摘要 0.2、其余步骤 0；接入点未配 temperature_override）。"""

    captured: list[dict[str, Any]] = []
    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, _llm_endpoints_value()),
    )

    def fake_post(url, **kwargs):
        captured.append(kwargs["payload"])
        return {"choices": [{"message": {"content": "OK"}}]}

    monkeypatch.setattr(settings_service, "post_chat_completion", fake_post)

    settings_service.test_llm_model("summary", "model-a", False)
    settings_service.test_llm_model("scoring", "model-a", False)
    settings_service.test_llm_model("beijing_gate", "model-a", False)

    assert captured[0]["temperature"] == 0.2, "摘要真实请求 temperature=0.2"
    assert captured[1]["temperature"] == 0.0
    assert captured[2]["temperature"] == 0


# ---------- 探针与真实请求一致（补丁：额外参数只留一份定义） ----------

# 真实请求路径涉及的 adapter 模块：各模块以名字导入了 post_chat_completion，
# 打桩要逐模块替换（只替换最底下的 HTTP 发送层）。
_REAL_ADAPTER_MODULES = (
    llm_summary_module,
    llm_source_module,
    llm_scoring_module,
    sentiment_classifier_module,
    external_filter_model_module,
    llm_beijing_gate_module,
    llm_duplicate_review_module,
    llm_prompt_optimizer_module,
) = (
    __import__("src.adapters.llm_summary", fromlist=["post_chat_completion"]),
    __import__("src.adapters.llm_source", fromlist=["post_chat_completion"]),
    __import__("src.adapters.llm_scoring", fromlist=["post_chat_completion"]),
    __import__("src.adapters.sentiment_classifier", fromlist=["post_chat_completion"]),
    __import__("src.adapters.external_filter_model", fromlist=["post_chat_completion"]),
    __import__("src.adapters.llm_beijing_gate", fromlist=["post_chat_completion"]),
    __import__("src.adapters.llm_duplicate_review", fromlist=["post_chat_completion"]),
    __import__("src.adapters.llm_prompt_optimizer", fromlist=["post_chat_completion"]),
)

# 各步骤桩响应：必须能通过该步骤真实调用路径自己的响应校验/解析
_STEP_RESPONSES = {
    "summary": "这是一段摘要。",
    "source": "北京日报",
    "sentiment": '{"label": "positive", "confidence": 0.9}',
    "scoring": "85",
    "external_filter": "78",
    "beijing_gate": '{"is_beijing_related": true, "reason": "北京市教委主办"}',
    "duplicate_review": '{"duplicate_groups": []}',
    "prompt_optimizer": '{"decision": "no_change", "note": "无需修改"}',
}

_PROBE_VS_REAL_CASES = [
    ("summary", None),
    ("source", None),
    ("sentiment", None),
    ("scoring", None),
    ("external_filter", None),
    ("beijing_gate", None),
    ("duplicate_review", None),
    ("prompt_optimizer", None),
    # 京内判定带 response_format：OpenRouter（json_schema）与非 OpenRouter
    # （json_object）接入点各测一例
    ("beijing_gate", "deepseek"),
]

_IGNORED_PAYLOAD_FIELDS = {"model", "messages", "max_tokens"}


def _run_real_step_call(step: str) -> None:
    """沿真实调用路径触发该步骤的 adapter 请求（只桩 HTTP 层）。"""

    from src.adapters import (
        external_filter_model,
        llm_beijing_gate,
        llm_duplicate_review,
        llm_prompt_optimizer,
        llm_scoring,
        llm_source,
        llm_summary,
        sentiment_classifier,
    )
    from src.domain import BeijingGateCandidate, ExternalFilterCandidate

    if step == "summary":
        llm_summary.summarise({"title": "标题", "content": "正文内容"})
    elif step == "source":
        llm_source.detect_source({"title": "标题", "content_markdown": "正文内容"})
    elif step == "scoring":
        llm_scoring.call_relevance_api("正文内容")
    elif step == "sentiment":
        sentiment_classifier.classify_sentiment("正文内容")
    elif step == "external_filter":
        external_filter_model.call_external_filter_model(
            ExternalFilterCandidate(
                article_id="a1",
                title="标题",
                source="北京日报",
                publish_time_iso=None,
                summary="摘要",
                content="正文内容",
                sentiment_label="positive",
                is_beijing_related=True,
                is_beijing_related_llm=True,
                external_importance_status="pending_external_filter",
            )
        )
    elif step == "beijing_gate":
        llm_beijing_gate.call_beijing_gate(
            BeijingGateCandidate(
                article_id="a1",
                title="标题",
                source="北京日报",
                publish_time_iso=None,
                summary="摘要",
                content="正文内容",
                sentiment_label="positive",
                is_beijing_related=None,
                is_beijing_related_llm=None,
                external_importance_status="ready_for_export",
            )
        )
    elif step == "duplicate_review":
        llm_duplicate_review.call_duplicate_review(
            [
                {
                    "article_id": "a1",
                    "title": "标题一",
                    "summary": "摘要一",
                    "source": "北京日报",
                }
            ]
        )
    elif step == "prompt_optimizer":
        llm_prompt_optimizer.call_prompt_optimizer(
            prompt_key="external_positive",
            current_content="旧内容",
            cases=[],
            reference_cases=[],
        )
    else:  # pragma: no cover - 参数化集合写错时立即暴露
        raise AssertionError(f"未知步骤：{step}")


@pytest.mark.parametrize(("step", "endpoint_key"), _PROBE_VS_REAL_CASES)
def test_step_probe_payload_matches_real_request_payload(
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    endpoint_key: str | None,
) -> None:
    """8 个步骤逐一对照：保存时探针与真实 adapter 调用的请求体，除
    model / messages / max_tokens 之外的字段完全相同。两边都来自真实
    调用路径，任何一边写死期望值都算不合格——这是「额外参数只有一份
    定义」的直接证据。"""

    from src.business_config import business_config_context
    from tests.conftest import DEEPSEEK_ENDPOINT, OPENROUTER_ENDPOINT, make_endpoint_config

    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "deepseek-key")
    config = make_endpoint_config(
        endpoints=(OPENROUTER_ENDPOINT, DEEPSEEK_ENDPOINT),
        default_endpoint="openrouter",
        step_endpoints={step: endpoint_key} if endpoint_key else {},
    )
    endpoints_value = {
        "default": "openrouter",
        "items": [
            {
                "key": "openrouter",
                "label": "OpenRouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "LLM_API_KEY",
                "api_style": "openrouter",
                "temperature_override": None,
                "default_model": "openrouter/default-model",
            },
            {
                "key": "deepseek",
                "label": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "LLM_DEEPSEEK_API_KEY",
                "api_style": "thinking",
                "temperature_override": None,
                "default_model": "deepseek-chat",
            },
        ],
    }
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, endpoints_value),
    )

    real_payloads: list[dict[str, Any]] = []
    probe_payloads: list[dict[str, Any]] = []
    response_content = _STEP_RESPONSES[step]

    def fake_real_post(url, **kwargs):
        real_payloads.append(kwargs["payload"])
        return {"choices": [{"message": {"content": response_content}}]}

    def fake_probe_post(url, **kwargs):
        probe_payloads.append(kwargs["payload"])
        return {"choices": [{"message": {"content": response_content}}]}

    with business_config_context(config):
        for module in _REAL_ADAPTER_MODULES:
            monkeypatch.setattr(module, "post_chat_completion", fake_real_post)
        _run_real_step_call(step)

        monkeypatch.setattr(settings_service, "post_chat_completion", fake_probe_post)
        settings_service.test_llm_model(
            step, f"{step}-model", True, endpoint=endpoint_key
        )

    assert len(real_payloads) == 1, "真实路径应恰好发出一次请求"
    assert len(probe_payloads) == 1, "探针应恰好发出一次请求"

    def fixed_fields(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in _IGNORED_PAYLOAD_FIELDS
        }

    assert fixed_fields(probe_payloads[0]) == fixed_fields(real_payloads[0])
    # 非空校验：两边确实带上了本步骤的固定参数（temperature 等）
    assert fixed_fields(real_payloads[0])
    if step == "beijing_gate":
        assert "response_format" in fixed_fields(real_payloads[0])


def test_probe_message_switches_to_json_only_for_response_format_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """带 response_format 的步骤实测用含 JSON 字样的探针消息（JSON 模式
    要求提示里出现 JSON 字样）；不带的步骤仍是「只回复 OK」。"""

    monkeypatch.setenv("LLM_API_KEY", "openrouter-key")
    monkeypatch.setenv("LLM_DEEPSEEK_API_KEY", "deepseek-key")
    _patch_settings_adapter(
        monkeypatch,
        _endpoints_section_adapter(monkeypatch, _llm_endpoints_value()),
    )
    captured: list[dict[str, Any]] = []

    def fake_post(url, **kwargs):
        captured.append(kwargs["payload"])
        return {"choices": [{"message": {"content": '{"ok": true}'}}]}

    monkeypatch.setattr(settings_service, "post_chat_completion", fake_post)

    settings_service.test_llm_model("beijing_gate", "gate-model", False)
    settings_service.test_llm_model("summary", "model-a", False)

    gate_message = captured[0]["messages"][0]["content"]
    assert "JSON" in gate_message
    assert gate_message != "只回复 OK"
    assert captured[1]["messages"][0]["content"] == "只回复 OK"
