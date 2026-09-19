"""Settings phase 2 import: strict parsing, skip-existing, source labels.

Mutation coverage:
- M9  import only writes sections missing from the database (pgserver test in
      test_console_settings_migration.py)
- M10 missing wordlist source files refuse ``--apply``
- M2/M3 at import time: empty wordlist files are rejected
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.console import settings_service


def _write_wordlists(tmp_path, *, bonuses: str | None = None) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "education_keywords.txt").write_text(
        "教育\n学校\n# comment\n\n", encoding="utf-8"
    )
    (config_dir / "beijing_keywords.txt").write_text("北京\n海淀\n", encoding="utf-8")
    (config_dir / "source_aliases.json").write_text(
        json.dumps(
            {"suffixes": ["客户端"], "aliases": {"北京号": "北京日报"}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if bonuses is None:
        bonuses = json.dumps({"教育工委": 100, "高考": 10}, ensure_ascii=False)
    (config_dir / "score_keyword_bonuses.json").write_text(bonuses, encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_service, "load_environment", lambda: None)
    for key in (
        "SCORE_KEYWORD_BONUSES",
        "KEYWORDS_PATH",
        "BEIJING_KEYWORDS_PATH",
        "SOURCE_ALIASES_PATH",
        "SCORE_KEYWORD_BONUSES_PATH",
        "CRAWL_SOURCES",
    ):
        monkeypatch.delenv(key, raising=False)


def _db_rows(sections: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"section": section, "value": value, "version": 3}
        for section, value in sections.items()
    ]


def _patch_db(monkeypatch: pytest.MonkeyPatch, existing: dict[str, Any]) -> list[str]:
    opened: list[str] = []

    def fetch_settings() -> list[dict[str, Any]]:
        opened.append("fetch_settings")
        return _db_rows(existing)

    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: SimpleNamespaceAppConfig(fetch_settings),
    )
    return opened


class SimpleNamespaceAppConfig:
    def __init__(self, fetch_settings) -> None:
        self.app_config = SimpleNamespaceWithRows(fetch_settings)


class SimpleNamespaceWithRows:
    def __init__(self, fetch_settings) -> None:
        self.fetch_settings = fetch_settings


WORDLIST_SECTIONS = (
    "score_keyword_bonuses",
    "education_keywords",
    "beijing_keywords",
    "source_aliases",
)


def test_preview_marks_write_and_exists_skip_with_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path)
    opened = _patch_db(
        monkeypatch,
        {"llm_models": {"default": "m"}, "crawl_sources": ["toutiao"]},
    )

    preview = settings_service.preview_legacy_import(root=tmp_path)

    assert opened == ["fetch_settings"]
    status = preview["sections_status"]
    assert status["llm_models"]["status"] == "exists_skip"
    assert status["crawl_sources"]["status"] == "exists_skip"
    for section in WORDLIST_SECTIONS:
        assert status[section]["status"] == "write", section
    assert status["score_keyword_bonuses"]["source"] == str(
        tmp_path / "config" / "score_keyword_bonuses.json"
    )
    assert status["education_keywords"]["source"] == str(
        tmp_path / "config" / "education_keywords.txt"
    )
    assert preview["wordlist_errors"] == []
    assert preview["sections"]["education_keywords"] == ["教育", "学校"]
    assert preview["sections"]["score_keyword_bonuses"] == [
        {"keyword": "教育工委", "bonus": 100},
        {"keyword": "高考", "bonus": 10},
    ]


def test_preview_bonus_env_var_wins_over_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path)
    monkeypatch.setenv("SCORE_KEYWORD_BONUSES", '{"环境词": 5}')
    _patch_db(monkeypatch, {})

    preview = settings_service.preview_legacy_import(root=tmp_path)

    assert preview["sections"]["score_keyword_bonuses"] == [
        {"keyword": "环境词", "bonus": 5}
    ]
    assert (
        preview["sections_status"]["score_keyword_bonuses"]["source"]
        == "环境变量 SCORE_KEYWORD_BONUSES"
    )


def test_preview_with_unusable_source_is_unresolved_and_never_opens_db(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path)
    (tmp_path / "config" / "education_keywords.txt").unlink()

    def forbidden():
        raise AssertionError("存在解析错误时预览不得打开数据库")

    monkeypatch.setattr(settings_service, "get_adapter", forbidden)

    preview = settings_service.preview_legacy_import(root=tmp_path)

    assert preview["wordlist_errors"]
    assert all(
        preview["sections_status"][section]["status"] == "unresolved"
        for section in preview["sections"]
    )


# ---------------------------------------------------------------------------
# M10 + strict parsing: --apply refuses on any unusable source


def test_apply_refuses_when_a_wordlist_file_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "education_keywords.txt").write_text("教育\n", encoding="utf-8")
    # beijing_keywords.txt / source_aliases.json / score_keyword_bonuses.json 缺失
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: pytest.fail("来源缺失时 --apply 不得打开数据库"),
    )

    with pytest.raises(ValueError, match="来源文件不存在"):
        settings_service.import_legacy_config(apply=True, root=tmp_path)


def test_apply_refuses_non_integer_bonus_and_names_the_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path, bonuses=json.dumps({"高考": "ten"}, ensure_ascii=False))
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: pytest.fail("严格解析失败时 --apply 不得打开数据库"),
    )

    with pytest.raises(ValueError, match="高考.*必须是整数"):
        settings_service.import_legacy_config(apply=True, root=tmp_path)


def test_apply_refuses_unparsable_bonus_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path, bonuses="{not-json")
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: pytest.fail("严格解析失败时 --apply 不得打开数据库"),
    )

    with pytest.raises(ValueError, match="无法解析为 JSON"):
        settings_service.import_legacy_config(apply=True, root=tmp_path)


def test_apply_refuses_invalid_source_aliases_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path)
    (tmp_path / "config" / "source_aliases.json").write_text(
        json.dumps({"suffixes": ["客户端"], "aliases": {"北京号": ""}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: pytest.fail("严格解析失败时 --apply 不得打开数据库"),
    )

    with pytest.raises(ValueError, match="aliases"):
        settings_service.import_legacy_config(apply=True, root=tmp_path)


@pytest.mark.parametrize("empty_file", ["education_keywords.txt", "beijing_keywords.txt"])
def test_apply_refuses_wordlist_file_that_normalizes_to_empty(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    empty_file: str,
) -> None:
    _write_wordlists(tmp_path)
    (tmp_path / "config" / empty_file).write_text("\n# only comments\n", encoding="utf-8")
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: pytest.fail("空词表不得写库"),
    )

    with pytest.raises(ValueError, match="规范化后不能为空"):
        settings_service.import_legacy_config(apply=True, root=tmp_path)


def test_apply_refuses_bonus_keywords_that_collide_after_strip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(
        tmp_path,
        bonuses=json.dumps({"高考": 10, " 高考 ": 20}, ensure_ascii=False),
    )
    monkeypatch.setattr(
        settings_service,
        "get_adapter",
        lambda: pytest.fail("校验拒绝的内容不得写库"),
    )

    with pytest.raises(ValueError, match="关键词重复"):
        settings_service.import_legacy_config(apply=True, root=tmp_path)


# ---------------------------------------------------------------------------
# happy path


def test_apply_writes_all_sections_through_the_incremental_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path)
    writes: list[dict[str, Any]] = []
    report = {
        "written_sections": list(WORDLIST_SECTIONS),
        "skipped_sections": ["llm_models", "crawl_sources"],
        "accounts_written": False,
    }

    class _Adapter:
        def __init__(self) -> None:
            self.app_config = SimpleNamespaceWithRows(
                lambda: _db_rows({"llm_models": {}, "crawl_sources": []})
            )

        def import_app_config_missing(self, **kwargs: Any) -> dict[str, Any]:
            writes.append(kwargs)
            return report

    adapter = _Adapter()
    monkeypatch.setattr(settings_service, "get_adapter", lambda: adapter)

    result = settings_service.import_legacy_config(apply=True, root=tmp_path)

    assert writes == [
        {
            "sections": result["sections"],
            "accounts": result["accounts"],
        }
    ]
    assert result["written_sections"] == list(WORDLIST_SECTIONS)
    assert result["sections"]["source_aliases"] == {
        "suffixes": ["客户端"],
        "aliases": {"北京号": "北京日报"},
    }


def test_preview_reports_existing_sections_and_wordlist_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _write_wordlists(tmp_path)
    _patch_db(
        monkeypatch,
        {"score_keyword_bonuses": [{"keyword": "旧词", "bonus": 1}]},
    )

    preview = settings_service.preview_legacy_import(root=tmp_path)

    status = preview["sections_status"]
    assert status["score_keyword_bonuses"]["status"] == "exists_skip"
    assert status["education_keywords"]["status"] == "write"
    assert status["beijing_keywords"]["status"] == "write"
    assert status["source_aliases"]["status"] == "write"
