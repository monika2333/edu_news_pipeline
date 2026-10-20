"""评分排除名单配置分区（score_exclusion_keywords）的单测。

覆盖校验器、配置加载、快照与分区注册。worker 的排除行为在
``tests/test_score_worker.py``；设置页行为在 jsdom 套件。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src import business_config
from src.business_config import (
    BusinessConfigError,
    load_business_config,
)
from tests.conftest import make_endpoint_config


def _endpoint_items_value() -> dict[str, Any]:
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
            }
        ],
    }


def _settings_rows(exclusion_value: Any | None = None) -> list[dict[str, Any]]:
    return [
        {
            "section": "llm_models",
            "value": {
                "default": "default-model",
                "steps": {
                    step: {"model": None, "reasoning": False}
                    for step in (
                        "summary",
                        "source",
                        "sentiment",
                        "scoring",
                        "external_filter",
                        "beijing_gate",
                        "duplicate_review",
                    )
                },
            },
            "version": 1,
        },
        {
            "section": "llm_endpoints",
            "value": _endpoint_items_value(),
            "version": 1,
        },
        {"section": "crawl_sources", "value": ["toutiao"], "version": 1},
        {"section": "score_keyword_bonuses", "value": [], "version": 1},
        {
            "section": "score_exclusion_keywords",
            "value": [] if exclusion_value is None else exclusion_value,
            "version": 6,
        },
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


def _fake_adapter(rows: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        app_config=SimpleNamespace(
            fetch_settings=lambda: rows,
            fetch_enabled_accounts=lambda: [],
        )
    )


class TestValidateScoreExclusionKeywords:
    def test_empty_list_is_allowed(self) -> None:
        assert business_config.validate_score_exclusion_keywords([]) == []

    def test_trim_preserves_order_and_drops_no_entries(self) -> None:
        assert business_config.validate_score_exclusion_keywords(
            [" 考试 ", "培训", " 竞赛"]
        ) == ["考试", "培训", "竞赛"]

    def test_duplicate_after_trim_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="关键词重复"):
            business_config.validate_score_exclusion_keywords(
                [" 考试 ", "考试"]
            )

    @pytest.mark.parametrize(
        ("value", "message"),
        [
            ([""], "不能为空"),
            (["  "], "不能为空"),
            ([1], "必须是字符串"),
            ({"考试": True}, "必须是有序数组"),
            ("考试", "必须是有序数组"),
            (None, "必须是有序数组"),
        ],
    )
    def test_invalid_values_are_rejected(self, value: Any, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            business_config.validate_score_exclusion_keywords(value)

    def test_section_is_registered(self) -> None:
        assert "score_exclusion_keywords" in business_config.SETTING_SECTIONS
        assert "score_exclusion_keywords" in business_config.SECTION_VALIDATORS
        assert "validate_score_exclusion_keywords" in business_config.__all__
        assert business_config.validate_section(
            "score_exclusion_keywords", [" 考试 "]
        ) == ["考试"]


class TestLoadAndSnapshot:
    def test_load_reads_the_section_and_snapshot_keeps_configured_order(self) -> None:
        loaded = load_business_config(
            _fake_adapter(_settings_rows([" 考试 ", "培训"]))
        )

        assert loaded.score_exclusion_keywords == ("考试", "培训")
        assert loaded.versions["score_exclusion_keywords"] == 6
        assert loaded.snapshot()["score_exclusion_keywords"] == ["考试", "培训"]

    def test_missing_section_fails_by_name(self) -> None:
        rows = [
            row
            for row in _settings_rows()
            if row["section"] != "score_exclusion_keywords"
        ]

        with pytest.raises(BusinessConfigError, match="score_exclusion_keywords"):
            load_business_config(_fake_adapter(rows))

    def test_invalid_stored_value_fails_load(self) -> None:
        with pytest.raises(BusinessConfigError, match="数据库业务配置无效"):
            load_business_config(_fake_adapter(_settings_rows(["考试", "考试"])))

    def test_default_config_excludes_nothing(self) -> None:
        config = make_endpoint_config()

        assert config.score_exclusion_keywords == ()
        assert config.snapshot()["score_exclusion_keywords"] == []
