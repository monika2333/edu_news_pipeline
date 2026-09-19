from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src import business_config
from src.config import get_settings

ALLOWED_HOSTS = ("openrouter.ai", "api.deepseek.com", "open.bigmodel.cn")


def _endpoint_items_value() -> dict[str, object]:
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
            },
            {
                "key": "deepseek",
                "label": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "LLM_DEEPSEEK_API_KEY",
                "api_style": "thinking",
                "temperature_override": 0.5,
            },
        ],
    }


def _settings_rows() -> list[dict[str, object]]:
    return [
        {
            "section": "llm_models",
            "value": {
                "default": "default-model",
                "steps": {
                    "summary": {"model": None, "reasoning": False},
                    "source": {"model": "source-model", "reasoning": True},
                    "sentiment": {"model": None, "reasoning": True},
                    "scoring": {"model": "scoring-model", "reasoning": True},
                    "external_filter": {"model": None, "reasoning": False},
                    "beijing_gate": {"model": "gate-model", "reasoning": True},
                    "duplicate_review": {"model": "duplicate-model", "reasoning": False},
                },
            },
            "version": 3,
        },
        {
            "section": "llm_endpoints",
            "value": _endpoint_items_value(),
            "version": 2,
        },
        {
            "section": "crawl_sources",
            "value": ["tencent", "toutiao"],
            "version": 5,
        },
        {
            "section": "score_keyword_bonuses",
            "value": [
                {"keyword": "教育工委", "bonus": 100},
                {"keyword": "高考", "bonus": 10},
            ],
            "version": 1,
        },
        {
            "section": "education_keywords",
            "value": ["教育", "学校"],
            "version": 1,
        },
        {
            "section": "beijing_keywords",
            "value": ["北京", "海淀"],
            "version": 1,
        },
        {
            "section": "source_aliases",
            "value": {"suffixes": ["客户端"], "aliases": {"北京号": "北京日报"}},
            "version": 1,
        },
    ]


@pytest.fixture(autouse=True)
def _allowed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(get_settings(), llm_allowed_hosts=ALLOWED_HOSTS)
    monkeypatch.setattr("src.config.get_settings", lambda: settings)


def test_m1_null_steps_follow_default_and_explicit_steps_win() -> None:
    resolved = business_config.resolve_llm_steps(_settings_rows()[0]["value"])

    assert resolved["summary"].model == "default-model"
    assert resolved["external_filter"].model == "default-model"
    assert resolved["source"].model == "source-model"
    assert resolved["duplicate_review"].model == "duplicate-model"
    assert resolved["summary"].reasoning is False
    assert resolved["source"].reasoning is True


@pytest.mark.parametrize(
    "source",
    ["bjrb", "laodongwubao", "ldwb", "beijingdaily"],
)
def test_m7_hourly_sources_reject_daily_only_keys_and_aliases(source: str) -> None:
    with pytest.raises(ValueError, match="仅允许每日单独任务"):
        business_config.validate_crawl_sources([source])


def test_m10_runtime_loads_only_enabled_account_query_results() -> None:
    enabled = {
        "id": "account-1",
        "source": "toutiao",
        "normalized_identifier": "enabled-token",
        "original_input": "enabled-token",
        "profile_url": "https://www.toutiao.com/c/user/token/enabled-token/",
        "display_name": None,
    }
    app_config = SimpleNamespace(
        fetch_settings=lambda: _settings_rows(),
        fetch_enabled_accounts=lambda: [enabled],
    )

    loaded = business_config.load_business_config(
        SimpleNamespace(app_config=app_config)
    )

    assert [item.normalized_identifier for item in loaded.accounts["toutiao"]] == [
        "enabled-token"
    ]
    assert loaded.accounts["tencent"] == ()


def test_m12_missing_database_section_fails_without_default() -> None:
    app_config = SimpleNamespace(
        fetch_settings=lambda: _settings_rows()[:1],
        fetch_enabled_accounts=lambda: [],
    )

    with pytest.raises(
        business_config.BusinessConfigError,
        match="crawl_sources",
    ):
        business_config.load_business_config(SimpleNamespace(app_config=app_config))


def test_m17_legacy_environment_and_files_emit_individual_warnings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("src.config.load_environment", lambda: None)
    monkeypatch.setenv("LLM_MODEL", "ignored-model")
    monkeypatch.setenv("LLM_REASONING_ENABLED", "false")
    old_file = tmp_path / "config/toutiao_author.txt"
    old_file.parent.mkdir()
    old_file.write_text("token", encoding="utf-8")

    messages = business_config.warn_legacy_config(root=tmp_path)

    assert any("LLM_MODEL" in message and "不再生效" in message for message in messages)
    assert any(
        "LLM_REASONING_ENABLED" in message and "不再生效" in message
        for message in messages
    )
    assert any(
        "config/toutiao_author.txt" in message and "不再生效" in message
        for message in messages
    )
    assert "LLM_MODEL" in caplog.text


def test_m21_crawl_sources_are_returned_in_catalog_order() -> None:
    result = business_config.validate_crawl_sources(
        ["gmw", "chinanews", "toutiao"]
    )

    assert result == ["toutiao", "chinanews", "gmw"]


def test_m22_crawl_source_aliases_are_normalized_before_catalog_sorting() -> None:
    result = business_config.validate_crawl_sources(["gmw", "qq", "toutiao"])

    assert result == ["toutiao", "tencent", "gmw"]


@pytest.mark.parametrize(
    ("sources", "expected"),
    [
        (["bjrb", "toutiao"], ["toutiao", "bjrb"]),
        (["ldwb", "bjrb", "toutiao"], ["toutiao", "bjrb", "ldwb"]),
    ],
)
def test_m23_daily_only_sources_follow_catalog_order_when_allowed(
    sources: list[str],
    expected: list[str],
) -> None:
    assert business_config.validate_crawl_sources(
        sources,
        allow_daily=True,
    ) == expected


def test_m24_normalize_source_list_preserves_caller_order() -> None:
    sources = ["gmw", "chinanews", "toutiao"]

    assert business_config.normalize_source_list(
        sources,
        allow_daily=False,
    ) == sources


@pytest.mark.parametrize(
    ("sources", "message"),
    [
        (["missing-source"], "未知来源"),
        (["toutiao", "toutiao"], "来源重复"),
        ([], "抓取来源列表不能为空"),
    ],
)
def test_m25_crawl_source_validation_rejects_invalid_lists_before_sorting(
    sources: list[str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        business_config.validate_crawl_sources(sources)


def test_endpoint_field_missing_is_read_as_null_and_normalized_output_carries_it() -> None:
    raw_value = _settings_rows()[0]["value"]
    raw_value["steps"]["summary"].pop("endpoint", None)

    normalized = business_config.validate_llm_models(raw_value)

    assert all(step["endpoint"] is None for step in normalized["steps"].values())
    resolved = business_config.resolve_llm_steps(raw_value)
    assert all(config.endpoint is None for config in resolved.values())


def test_step_endpoint_with_null_model_is_rejected_inside_llm_models() -> None:
    value = _settings_rows()[0]["value"]
    value["steps"]["duplicate_review"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "deepseek",
    }

    with pytest.raises(ValueError, match="指定了 endpoint 时 model 不能为空"):
        business_config.validate_llm_models(value)


def test_endpoint_requiring_model_message_names_the_binding_reason() -> None:
    value = _settings_rows()[0]["value"]
    value["steps"]["scoring"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "deepseek",
    }

    with pytest.raises(ValueError, match="模型名与接入点服务商绑定"):
        business_config.validate_llm_models(value)


def test_load_business_config_rejects_unknown_endpoint_reference() -> None:
    rows = _settings_rows()
    rows[0]["value"]["steps"]["duplicate_review"] = {
        "model": "duplicate-model",
        "reasoning": False,
        "endpoint": "ghost",
    }
    app_config = SimpleNamespace(
        fetch_settings=lambda: rows,
        fetch_enabled_accounts=lambda: [],
    )

    with pytest.raises(
        business_config.BusinessConfigError,
        match="ghost",
    ):
        business_config.load_business_config(SimpleNamespace(app_config=app_config))


def test_load_business_config_resolves_step_endpoints_and_default() -> None:
    rows = _settings_rows()
    rows[0]["value"]["steps"]["duplicate_review"] = {
        "model": "duplicate-model",
        "reasoning": False,
        "endpoint": "deepseek",
    }
    app_config = SimpleNamespace(
        fetch_settings=lambda: rows,
        fetch_enabled_accounts=lambda: [],
    )

    loaded = business_config.load_business_config(
        SimpleNamespace(app_config=app_config)
    )

    assert loaded.default_endpoint == "openrouter"
    assert loaded.endpoint_for_step("duplicate_review").key == "deepseek"
    assert loaded.endpoint_for_step("summary").key == "openrouter"
    assert loaded.resolved_endpoint_key("summary") == "openrouter"


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"base_url": "http://api.deepseek.com"}, "https"),
        ({"base_url": "https://evil.example.com/v1"}, "白名单"),
        ({"base_url": "https://api.deepseek.com:8443"}, "443"),
        ({"base_url": "https://api.deepseek.com/?x=1"}, "query"),
        ({"base_url": "https://api.deepseek.com#frag"}, "fragment"),
        ({"base_url": "https://api.deepseek.com:abc"}, "端口无效"),
        ({"api_key_env": "LLM_API_KEY_VALUE"}, "_API_KEY"),
        ({"api_key_env": "DEEPSEEK_PASSWORD"}, "_API_KEY"),
        ({"api_style": "openai"}, "api_style"),
        ({"temperature_override": 3.0}, "0 到 2"),
        ({"temperature_override": True}, "null 或 0-2"),
        ({"key": "OpenRouter"}, "key 只能"),
        ({"key": ""}, "key 只能"),
        ({"label": ""}, "label"),
        ({"label": "x" * 41}, "label"),
    ],
)
def test_validate_llm_endpoints_rejects_invalid_item_fields(
    patch: dict[str, object],
    message: str,
) -> None:
    value = _endpoint_items_value()
    value["items"][1] = {**value["items"][1], **patch}

    with pytest.raises(ValueError, match=message):
        business_config.validate_llm_endpoints(value)


def test_validate_llm_endpoints_strips_trailing_slash_and_preserves_path() -> None:
    value = _endpoint_items_value()
    value["items"][1]["base_url"] = "https://open.bigmodel.cn/api/paas/v4/"

    normalized = business_config.validate_llm_endpoints(value)

    assert normalized["items"][1]["base_url"] == "https://open.bigmodel.cn/api/paas/v4"


def test_validate_llm_endpoints_enforces_item_count_and_default() -> None:
    value = _endpoint_items_value()
    value["items"] = []
    with pytest.raises(ValueError, match="数量"):
        business_config.validate_llm_endpoints(value)

    value["items"] = [{"**missing**": True}]
    with pytest.raises(ValueError, match="必须是对象|含未知字段"):
        business_config.validate_llm_endpoints(value)

    value = _endpoint_items_value()
    value["default"] = "ghost"
    with pytest.raises(ValueError, match="default"):
        business_config.validate_llm_endpoints(value)


def test_llm_endpoints_is_a_managed_section_and_versioned() -> None:
    assert "llm_endpoints" in business_config.SETTING_SECTIONS
    assert "llm_endpoints" in business_config.SECTION_VALIDATORS

    rows = _settings_rows()
    app_config = SimpleNamespace(
        fetch_settings=lambda: rows,
        fetch_enabled_accounts=lambda: [],
    )
    loaded = business_config.load_business_config(
        SimpleNamespace(app_config=app_config)
    )

    assert loaded.versions["llm_endpoints"] == 2


def test_legacy_api_base_url_env_warns_as_expired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr("src.config.load_environment", lambda: None)
    monkeypatch.setenv("LLM_API_BASE_URL", "https://openrouter.ai/api/v1")

    messages = business_config.warn_legacy_config(root=tmp_path)

    assert any(
        "LLM_API_BASE_URL" in message and "不再生效" in message for message in messages
    )
