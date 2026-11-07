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
                "default_model": "default-model",
            },
            {
                "key": "deepseek",
                "label": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "api_key_env": "LLM_DEEPSEEK_API_KEY",
                "api_style": "thinking",
                "temperature_override": 0.5,
                "default_model": "deepseek-default",
            },
        ],
    }


def _llm_models_value() -> dict[str, object]:
    return {
        "steps": {
            # 双 null：跟随默认接入点，用它的 default_model
            "summary": {"model": None, "reasoning": False},
            # 接入点 + 模型：显式组合
            "source": {"model": "source-model", "reasoning": True, "endpoint": "openrouter"},
            # 接入点 + 空模型：用该接入点的 default_model
            "sentiment": {"model": None, "reasoning": True, "endpoint": "deepseek"},
            "scoring": {"model": "scoring-model", "reasoning": True, "endpoint": "deepseek"},
            "external_filter": {"model": None, "reasoning": False},
            "beijing_gate": {"model": "gate-model", "reasoning": True, "endpoint": "deepseek"},
            "duplicate_review": {
                "model": "duplicate-model",
                "reasoning": False,
                "endpoint": "openrouter",
            },
        },
    }


def _settings_rows() -> list[dict[str, object]]:
    return [
        {
            "section": "llm_models",
            "value": _llm_models_value(),
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
            "section": "score_exclusion_keywords",
            "value": [],
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
        {
            "section": "review_sort_keywords",
            "value": {"市教委": ["市教委"], "中小学": ["小学"], "高校": ["大学"]},
            "version": 1,
        },
    ]


def _resolved_endpoints() -> tuple[dict, str]:
    return business_config.resolve_llm_endpoints(_endpoint_items_value())


@pytest.fixture(autouse=True)
def _allowed_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = replace(get_settings(), llm_allowed_hosts=ALLOWED_HOSTS)
    monkeypatch.setattr("src.config.get_settings", lambda: settings)


def test_m1_null_steps_follow_default_and_explicit_steps_win() -> None:
    endpoints_items, default_key = _resolved_endpoints()
    resolved = business_config.resolve_llm_steps(
        _llm_models_value(),
        endpoints=endpoints_items,
        default_endpoint=default_key,
    )

    # 组合一（双 null）：跟随默认接入点，用它的 default_model
    assert resolved["summary"].model == "default-model"
    assert resolved["summary"].endpoint is None
    assert resolved["external_filter"].model == "default-model"
    # 组合二（接入点 + 空模型）：用所选接入点的 default_model
    assert resolved["sentiment"].model == "deepseek-default"
    assert resolved["sentiment"].endpoint == "deepseek"
    # 组合三（接入点 + 模型）：显式组合原样生效
    assert resolved["scoring"].model == "scoring-model"
    assert resolved["scoring"].endpoint == "deepseek"
    assert resolved["source"].model == "source-model"
    assert resolved["duplicate_review"].model == "duplicate-model"
    assert resolved["summary"].reasoning is False
    assert resolved["source"].reasoning is True


def test_resolve_models_returns_concrete_names_for_all_steps() -> None:
    endpoints_items, default_key = _resolved_endpoints()

    models = business_config.resolve_models(
        _llm_models_value(),
        endpoints=endpoints_items,
        default_endpoint=default_key,
    )

    assert models == {
        "summary": "default-model",
        "source": "source-model",
        "sentiment": "deepseek-default",
        "scoring": "scoring-model",
        "external_filter": "default-model",
        "beijing_gate": "gate-model",
        "duplicate_review": "duplicate-model",
        "prompt_optimizer": "default-model",
    }


def test_step_with_model_but_no_endpoint_is_rejected() -> None:
    value = _llm_models_value()
    value["steps"]["duplicate_review"] = {
        "model": "duplicate-model",
        "reasoning": False,
        "endpoint": None,
    }

    with pytest.raises(ValueError, match="查重复核"):
        business_config.validate_llm_models(value)


def test_step_model_without_endpoint_message_names_the_binding_reason() -> None:
    value = _llm_models_value()
    value["steps"]["scoring"] = {
        "model": "scoring-model",
        "reasoning": True,
        "endpoint": None,
    }

    with pytest.raises(ValueError, match="模型名与接入点服务商绑定"):
        business_config.validate_llm_models(value)


def test_top_level_default_is_rejected() -> None:
    value = _llm_models_value()
    value["default"] = "legacy-default"

    with pytest.raises(ValueError, match="未知字段：default"):
        business_config.validate_llm_models(value)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"default_model": None}, "default_model 必须是非空模型名"),
        ({"default_model": "   "}, "default_model 必须是非空模型名"),
        ({"default_model": "x" * 301}, "长度不能超过 300"),
    ],
)
def test_endpoint_default_model_is_required_and_bounded(
    patch: dict[str, object],
    message: str,
) -> None:
    value = _endpoint_items_value()
    value["items"][1] = {**value["items"][1], **patch}

    with pytest.raises(ValueError, match=message):
        business_config.validate_llm_endpoints(value)


def test_default_model_missing_field_is_rejected() -> None:
    value = _endpoint_items_value()
    del value["items"][0]["default_model"]

    with pytest.raises(ValueError, match="default_model 必须是非空模型名"):
        business_config.validate_llm_endpoints(value)


def test_default_model_is_stripped_in_normalized_output() -> None:
    value = _endpoint_items_value()
    value["items"][1]["default_model"] = "  deepseek-default  "

    normalized = business_config.validate_llm_endpoints(value)

    assert normalized["items"][1]["default_model"] == "deepseek-default"


def test_endpoint_field_missing_is_read_as_null_and_normalized_output_carries_it() -> None:
    raw_value = _llm_models_value()
    raw_value["steps"]["summary"].pop("endpoint", None)

    normalized = business_config.validate_llm_models(raw_value)

    assert normalized["steps"]["summary"]["endpoint"] is None
    endpoints_items, default_key = _resolved_endpoints()
    resolved = business_config.resolve_llm_steps(
        raw_value,
        endpoints=endpoints_items,
        default_endpoint=default_key,
    )
    assert resolved["summary"].endpoint is None


def test_step_endpoint_with_null_model_is_legal() -> None:
    value = _llm_models_value()
    value["steps"]["duplicate_review"] = {
        "model": None,
        "reasoning": True,
        "endpoint": "deepseek",
    }

    normalized = business_config.validate_llm_models(value)

    assert normalized["steps"]["duplicate_review"] == {
        "model": None,
        "reasoning": True,
        "endpoint": "deepseek",
    }


def test_missing_prompt_optimizer_step_gets_documented_default() -> None:
    normalized = business_config.validate_llm_models(_llm_models_value())

    assert normalized["steps"]["prompt_optimizer"] == {
        "model": None,
        "reasoning": True,
        "endpoint": None,
    }
    endpoints_items, default_key = _resolved_endpoints()
    resolved = business_config.resolve_llm_steps(
        _llm_models_value(),
        endpoints=endpoints_items,
        default_endpoint=default_key,
    )
    assert resolved["prompt_optimizer"].model == "default-model"
    assert resolved["prompt_optimizer"].reasoning is True


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
    app_config = SimpleNamespace(
        fetch_settings=lambda: _settings_rows(),
        fetch_enabled_accounts=lambda: [],
    )

    loaded = business_config.load_business_config(
        SimpleNamespace(app_config=app_config)
    )

    assert loaded.default_endpoint == "openrouter"
    assert loaded.endpoint_for_step("duplicate_review").key == "openrouter"
    assert loaded.endpoint_for_step("sentiment").key == "deepseek"
    assert loaded.endpoint_for_step("summary").key == "openrouter"
    assert loaded.resolved_endpoint_key("summary") == "openrouter"
    assert loaded.resolved_endpoint_key("sentiment") == "deepseek"
    assert loaded.model_for("summary") == "default-model"
    assert loaded.model_for("sentiment") == "deepseek-default"


def test_snapshot_records_resolved_models_and_endpoint_keys() -> None:
    app_config = SimpleNamespace(
        fetch_settings=lambda: _settings_rows(),
        fetch_enabled_accounts=lambda: [],
    )

    loaded = business_config.load_business_config(
        SimpleNamespace(app_config=app_config)
    )

    snapshot_models = loaded.snapshot()["llm_models"]
    assert snapshot_models["summary"] == {
        "model": "default-model",
        "reasoning": False,
        "endpoint": "openrouter",
    }
    assert snapshot_models["sentiment"] == {
        "model": "deepseek-default",
        "reasoning": True,
        "endpoint": "deepseek",
    }


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"base_url": "http://api.deepseek.com"}, "https"),
        ({"base_url": "https://evil.example.com/v1"}, "允许列表内"),
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


def _rows_with_endpoint(rows: list[dict], item: dict) -> list[dict]:
    """往 llm_endpoints 分区追加一个接入点（用于构造白名单外接入点场景）。"""

    out = []
    for row in rows:
        if row["section"] == "llm_endpoints":
            value = {
                "default": row["value"]["default"],
                "items": [*row["value"]["items"], item],
            }
            out.append({**row, "value": value})
        else:
            out.append(row)
    return out


# ALLOWED_HOSTS 夹具只放三个域名：api.moonshot.cn（2026-11 起在完整默认列表里，
# 但不在这个夹具里）在此充当「白名单外域名」。
_BLOCKED_ITEM = {
    "key": "moonshot",
    "label": "Kimi",
    "base_url": "https://api.moonshot.cn/v1",
    "api_key_env": "MOONSHOT_API_KEY",
    "api_style": "thinking",
    "temperature_override": None,
    "default_model": "kimi-model",
}


def test_endpoint_host_allowed_matches_lowercase_exact() -> None:
    assert business_config.endpoint_host_allowed(
        "https://API.DeepSeek.com/v1", allowed_hosts=ALLOWED_HOSTS
    )
    assert not business_config.endpoint_host_allowed(
        "https://api.moonshot.cn/v1", allowed_hosts=ALLOWED_HOSTS
    )
    assert not business_config.endpoint_host_allowed(
        "https://openrouter.ai.evil.example/v1", allowed_hosts=ALLOWED_HOSTS
    )


def test_validate_llm_endpoints_can_skip_host_allowlist_for_saved_configs() -> None:
    """读取已保存配置只做结构校验：白名单外的接入点照常解析出来。"""

    value = _endpoint_items_value()
    value["items"].append(_BLOCKED_ITEM)

    normalized = business_config.validate_llm_endpoints(
        value, require_host_allowed=False
    )

    assert normalized["items"][-1]["base_url"] == "https://api.moonshot.cn/v1"
    with pytest.raises(ValueError, match="允许列表内"):
        business_config.validate_llm_endpoints(value)


def test_load_business_config_warns_but_loads_when_blocked_endpoint_unused(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """没有步骤在用的白名单外接入点：加载成功，只记一条警告。"""

    app_config = SimpleNamespace(
        fetch_settings=lambda: _rows_with_endpoint(_settings_rows(), _BLOCKED_ITEM),
        fetch_enabled_accounts=lambda: [],
    )

    with caplog.at_level("WARNING"):
        loaded = business_config.load_business_config(
            SimpleNamespace(app_config=app_config)
        )

    assert "moonshot" in loaded.llm_endpoints
    assert any("api.moonshot.cn" in record.message for record in caplog.records)


def test_load_business_config_fails_when_step_uses_blocked_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """被步骤使用的白名单外接入点：加载失败，报错写明域名与处理办法。"""

    rows = _rows_with_endpoint(_settings_rows(), _BLOCKED_ITEM)
    rows = [
        {
            **row,
            "value": (
                {
                    **row["value"],
                    "steps": {
                        **row["value"]["steps"],
                        # 让「来源识别」显式使用白名单外的接入点
                        "source": {
                            "model": None,
                            "reasoning": True,
                            "endpoint": "moonshot",
                        },
                    },
                }
                if row["section"] == "llm_models"
                else row["value"]
            ),
        }
        for row in rows
    ]
    app_config = SimpleNamespace(
        fetch_settings=lambda: rows,
        fetch_enabled_accounts=lambda: [],
    )

    with pytest.raises(business_config.BusinessConfigError) as excinfo:
        business_config.load_business_config(SimpleNamespace(app_config=app_config))

    message = str(excinfo.value)
    assert "api.moonshot.cn" in message
    assert "来源识别" in message
    assert "LLM_ALLOWED_HOSTS" in message
