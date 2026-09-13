from __future__ import annotations

from types import SimpleNamespace

import pytest

from src import business_config


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
            "section": "crawl_sources",
            "value": ["tencent", "toutiao"],
            "version": 5,
        },
    ]


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
