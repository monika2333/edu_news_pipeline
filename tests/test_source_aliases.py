from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from src import config
from src.domain import load_source_aliases, normalize_source_name
from src.workers import enrich_summary


@pytest.fixture
def source_aliases_path(tmp_path: Path) -> Path:
    path = tmp_path / "source_aliases.json"
    path.write_text(
        json.dumps(
            {
                "suffixes": ["客户端"],
                "aliases": {"北京号": "北京日报"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _normalize(source: Optional[str], path: Path) -> Optional[str]:
    return normalize_source_name(source, load_source_aliases(path))


def test_exact_alias_is_replaced(source_aliases_path: Path) -> None:
    assert _normalize("北京号", source_aliases_path) == "北京日报"


def test_suffix_is_stripped_once(source_aliases_path: Path) -> None:
    assert _normalize("北京日报客户端", source_aliases_path) == "北京日报"
    assert _normalize("北京日报客户端客户端", source_aliases_path) == "北京日报客户端"


def test_suffix_is_stripped_before_alias_lookup(source_aliases_path: Path) -> None:
    assert _normalize("北京号客户端", source_aliases_path) == "北京日报"


def test_empty_result_after_suffix_stripping_preserves_original(
    source_aliases_path: Path,
) -> None:
    assert _normalize("客户端", source_aliases_path) == "客户端"


def test_alias_matching_requires_full_string_equality(
    source_aliases_path: Path,
) -> None:
    assert _normalize("南京号外北京号", source_aliases_path) == "南京号外北京号"


def test_unmatched_source_is_preserved(source_aliases_path: Path) -> None:
    assert _normalize("中国教育报", source_aliases_path) == "中国教育报"


@pytest.mark.parametrize("source", [None, "", " \t "])
def test_none_or_blank_source_is_safe_and_preserved(
    source: Optional[str],
    source_aliases_path: Path,
) -> None:
    assert _normalize(source, source_aliases_path) == source


def test_missing_config_preserves_source_without_raising(tmp_path: Path) -> None:
    missing_path = tmp_path / "missing-source-aliases.json"

    assert _normalize("北京号客户端", missing_path) == "北京号客户端"


def test_invalid_config_preserves_source_without_raising(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid-source-aliases.json"
    invalid_path.write_text("{invalid", encoding="utf-8")

    assert _normalize("北京号客户端", invalid_path) == "北京号客户端"


def test_settings_resolves_source_aliases_path_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    custom_path = tmp_path / "custom-source-aliases.json"
    monkeypatch.setenv("SOURCE_ALIASES_PATH", str(custom_path))
    config.get_settings.cache_clear()

    try:
        assert config.get_settings().source_aliases_path == custom_path.resolve()
    finally:
        config.get_settings.cache_clear()


def test_worker_normalizes_source_and_logs_changed_value(
    monkeypatch: pytest.MonkeyPatch,
    source_aliases_path: Path,
) -> None:
    messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        enrich_summary,
        "detect_source",
        lambda article: {"llm_source": "北京号客户端"},
    )
    monkeypatch.setattr(
        enrich_summary,
        "log_info",
        lambda worker, message: messages.append((worker, message)),
    )

    result = enrich_summary._detect_article_source(
        {"article_id": "article-1", "content_markdown": "正文"},
        load_source_aliases(source_aliases_path),
    )

    assert result.llm_source == "北京日报"
    assert messages == [
        (
            "enrich_summary",
            "NORMALIZED source name article_id=article-1 "
            "original='北京号客户端' normalized='北京日报'",
        )
    ]
