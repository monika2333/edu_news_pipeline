"""审阅页自动排序关键词分区（review_sort_keywords）。

覆盖分区校验器、页面渲染路径的解析回退（resolve_review_sort_keywords）、
设置保存链路的分区注册、迁移种子与代码默认词表的一致性，以及审阅页模板
对词表的嵌入。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src import business_config
from src.business_config import (
    REVIEW_SORT_CATEGORIES,
    REVIEW_SORT_KEYWORD_DEFAULTS,
    load_business_config,
    resolve_review_sort_keywords,
    validate_review_sort_keywords,
)
from src.console import settings_service, web_routes
from src.console.app import create_app
from src.console.security import ConsoleUser, require_console_user

ROOT = Path(__file__).parents[1]
SORT_KEYWORDS_MIGRATION_PATH = (
    ROOT
    / "database"
    / "migrations"
    / "20260923120000_add_review_sort_keywords_setting.sql"
)


def _rules(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "市教委": ["市教委"],
        "中小学": ["小学"],
        "高校": ["大学"],
    }
    value.update(overrides)
    return value


class TestValidateReviewSortKeywords:
    def test_valid_value_is_normalized_and_key_order_fixed(self) -> None:
        value = {
            "高校": [" 大学 ", "学院"],
            "市教委": ["市教委"],
            "中小学": [],
        }

        normalized = validate_review_sort_keywords(value)

        # 键序固定为优先级顺序，词保留原样（仅去首尾空白），空类别合法
        assert normalized == {
            "市教委": ["市教委"],
            "中小学": [],
            "高校": ["大学", "学院"],
        }
        assert list(normalized) == list(REVIEW_SORT_CATEGORIES)

    def test_value_must_be_a_mapping(self) -> None:
        with pytest.raises(ValueError, match="必须是对象"):
            validate_review_sort_keywords(["市教委"])

    def test_unknown_category_is_rejected(self) -> None:
        value = _rules(**{"其他": ["杂项"]})
        with pytest.raises(ValueError, match="含未知类别"):
            validate_review_sort_keywords(value)

    def test_missing_category_is_rejected(self) -> None:
        value = _rules()
        del value["中小学"]
        with pytest.raises(ValueError, match="缺少类别"):
            validate_review_sort_keywords(value)

    def test_non_list_category_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="必须是有序数组"):
            validate_review_sort_keywords(_rules(**{"高校": "大学"}))

    def test_blank_or_non_string_keyword_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            validate_review_sort_keywords(_rules(**{"高校": ["大学", "  "]}))
        with pytest.raises(ValueError, match="不能为空"):
            validate_review_sort_keywords(_rules(**{"高校": ["大学", 3]}))

    def test_duplicate_keyword_within_category_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="关键词重复"):
            validate_review_sort_keywords(_rules(**{"高校": ["大学", " 大学 "]}))

    def test_duplicate_keyword_across_categories_is_rejected(self) -> None:
        # 优先级高的类别先命中，低优先级里的重复词永远不生效
        with pytest.raises(ValueError, match="同时出现在 市教委 和 高校"):
            validate_review_sort_keywords(_rules(**{"高校": ["市教委"]}))

    def test_duplicate_keyword_check_is_case_insensitive(self) -> None:
        # 前端匹配对大小写不敏感，"K12" 与 "k12" 语义相同
        with pytest.raises(ValueError, match="关键词重复"):
            validate_review_sort_keywords(_rules(**{"中小学": ["k12", "K12"]}))


class TestResolveReviewSortKeywords:
    def test_valid_row_passes_through(self) -> None:
        value = _rules()

        assert resolve_review_sort_keywords(value) == value

    def test_none_row_falls_back_to_defaults(self) -> None:
        resolved = resolve_review_sort_keywords(None)

        assert resolved == {
            category: list(words)
            for category, words in REVIEW_SORT_KEYWORD_DEFAULTS.items()
        }

    def test_invalid_row_falls_back_to_defaults(self) -> None:
        resolved = resolve_review_sort_keywords({"市教委": ["大学"], "中小学": []})

        assert resolved["市教委"] == list(REVIEW_SORT_KEYWORD_DEFAULTS["市教委"])
        assert resolved["高校"] == list(REVIEW_SORT_KEYWORD_DEFAULTS["高校"])

    def test_result_is_a_copy_and_mutation_cannot_leak_into_defaults(self) -> None:
        resolved = resolve_review_sort_keywords(None)
        resolved["市教委"].append("脏词")

        assert "脏词" not in resolve_review_sort_keywords(None)["市教委"]


class TestSettingsSavePath:
    def _fake_adapter(
        self,
        row: dict[str, Any],
        saves: list[dict[str, Any]],
    ) -> Any:
        from types import SimpleNamespace

        app_config = SimpleNamespace(
            fetch_setting=lambda section: row,
            update_app_setting_as_user=(
                lambda *, section, value, expected_version, actor_user_id: (
                    saves.append(
                        {
                            "section": section,
                            "value": value,
                            "expected_version": expected_version,
                            "actor_user_id": actor_user_id,
                        }
                    )
                    or {"section": section, "value": value, "version": expected_version + 1}
                )
            ),
        )
        return SimpleNamespace(app_config=app_config)

    def _admin(self) -> ConsoleUser:
        return ConsoleUser(
            method="test",
            user_id="admin-1",
            username="admin",
            display_name="管理员",
            role="admin",
        )

    def test_update_setting_saves_normalized_rules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        saves: list[dict[str, Any]] = []
        row = {"section": "review_sort_keywords", "value": _rules(), "version": 3}
        monkeypatch.setattr(
            settings_service, "get_adapter", lambda: self._fake_adapter(row, saves)
        )

        result = settings_service.update_setting(
            "review_sort_keywords",
            value=_rules(**{"高校": [" 大学 ", "学院"]}),
            expected_version=3,
            actor=self._admin(),
        )

        assert result["version"] == 4
        assert len(saves) == 1
        assert saves[0]["section"] == "review_sort_keywords"
        assert saves[0]["value"] == _rules(**{"高校": ["大学", "学院"]})

    def test_update_setting_rejects_cross_category_duplicate_before_save(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saves: list[dict[str, Any]] = []
        row = {"section": "review_sort_keywords", "value": _rules(), "version": 3}
        monkeypatch.setattr(
            settings_service, "get_adapter", lambda: self._fake_adapter(row, saves)
        )

        with pytest.raises(ValueError, match="同时出现在"):
            settings_service.update_setting(
                "review_sort_keywords",
                value=_rules(**{"高校": ["市教委"]}),
                expected_version=3,
                actor=self._admin(),
            )
        assert saves == []


class TestLoadAndSnapshot:
    def _rows(self) -> list[dict[str, Any]]:
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
                "value": {
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
                },
                "version": 1,
            },
            {"section": "crawl_sources", "value": ["toutiao"], "version": 1},
            {"section": "score_keyword_bonuses", "value": [], "version": 1},
            {"section": "education_keywords", "value": ["教育"], "version": 1},
            {"section": "beijing_keywords", "value": ["北京"], "version": 1},
            {
                "section": "source_aliases",
                "value": {"suffixes": [], "aliases": {}},
                "version": 1,
            },
            {
                "section": "review_sort_keywords",
                "value": _rules(),
                "version": 6,
            },
        ]

    def _fake_adapter(self, rows: list[dict[str, Any]]) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(
            app_config=SimpleNamespace(
                fetch_settings=lambda: rows,
                fetch_enabled_accounts=lambda: [],
            )
        )

    def test_load_builds_tuple_valued_rules_and_snapshot_records_content(self) -> None:
        loaded = load_business_config(self._fake_adapter(self._rows()))

        assert loaded.review_sort_keywords == {
            "市教委": ("市教委",),
            "中小学": ("小学",),
            "高校": ("大学",),
        }
        assert loaded.versions["review_sort_keywords"] == 6
        assert loaded.snapshot()["review_sort_keywords"] == _rules()

    def test_load_reports_missing_section_by_name(self) -> None:
        rows = [row for row in self._rows() if row["section"] != "review_sort_keywords"]

        from src.business_config import BusinessConfigError

        with pytest.raises(BusinessConfigError, match="review_sort_keywords"):
            load_business_config(self._fake_adapter(rows))


class TestReviewPageEmbed:
    def _render_admin_review(self, adapter: Any) -> str:
        app = create_app()
        app.dependency_overrides[require_console_user] = lambda: ConsoleUser(
            method="test",
            user_id="admin-id",
            username="admin",
            display_name="测试管理员",
            role="admin",
        )
        original_get_adapter = web_routes.get_adapter
        web_routes.get_adapter = lambda: adapter
        try:
            response = TestClient(app).get("/admin/review")
        finally:
            web_routes.get_adapter = original_get_adapter
        assert response.status_code == 200
        return response.text

    def _embedded_rules(self, html: str) -> dict[str, Any]:
        match = re.search(
            r'<script type="application/json" id="review-sort-rules">(.*?)</script>',
            html,
            re.DOTALL,
        )
        assert match, "审阅页缺少 review-sort-rules 嵌入"
        return json.loads(match.group(1))

    def test_page_embeds_database_rules_for_all_three_render_paths(self) -> None:
        class _Adapter:
            class app_config:  # noqa: N801
                @staticmethod
                def fetch_setting(section: str) -> dict[str, Any] | None:
                    assert section == "review_sort_keywords"
                    return {"section": section, "value": _rules(), "version": 9}

        html = self._render_admin_review(_Adapter())

        assert self._embedded_rules(html) == _rules()

    def test_page_falls_back_to_defaults_when_row_is_missing(self) -> None:
        class _NoRowAdapter:
            class app_config:  # noqa: N801
                @staticmethod
                def fetch_setting(section: str) -> dict[str, Any] | None:
                    return None

        html = self._render_admin_review(_NoRowAdapter())

        assert self._embedded_rules(html) == {
            category: list(words)
            for category, words in REVIEW_SORT_KEYWORD_DEFAULTS.items()
        }

    def test_page_falls_back_to_defaults_when_database_is_unavailable(self) -> None:
        class _BrokenAdapter:
            class app_config:  # noqa: N801
                @staticmethod
                def fetch_setting(section: str) -> dict[str, Any] | None:
                    raise RuntimeError("db down")

        html = self._render_admin_review(_BrokenAdapter())

        assert self._embedded_rules(html) == {
            category: list(words)
            for category, words in REVIEW_SORT_KEYWORD_DEFAULTS.items()
        }


def test_migration_seed_matches_code_defaults() -> None:
    source = SORT_KEYWORDS_MIGRATION_PATH.read_text(encoding="utf-8")
    match = re.search(r"'(\{.*?\})'::jsonb", source, re.DOTALL)
    assert match, "迁移文件缺少 review_sort_keywords 播种值"

    seed = json.loads(match.group(1))

    assert seed == {
        category: list(words)
        for category, words in REVIEW_SORT_KEYWORD_DEFAULTS.items()
    }
