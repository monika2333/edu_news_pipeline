from __future__ import annotations

from typing import Optional

import pytest

from src.domain import SourceAliasRules, normalize_source_name
from src.workers import enrich_summary


@pytest.fixture
def source_aliases() -> SourceAliasRules:
    return SourceAliasRules(
        suffixes=("客户端",),
        aliases={"北京号": "北京日报"},
    )


def _normalize(
    source: Optional[str],
    rules: SourceAliasRules,
) -> Optional[str]:
    return normalize_source_name(source, rules)


def test_exact_alias_is_replaced(source_aliases: SourceAliasRules) -> None:
    assert _normalize("北京号", source_aliases) == "北京日报"


def test_suffix_is_stripped_once(source_aliases: SourceAliasRules) -> None:
    assert _normalize("北京日报客户端", source_aliases) == "北京日报"
    assert _normalize("北京日报客户端客户端", source_aliases) == "北京日报客户端"


def test_suffix_is_stripped_before_alias_lookup(
    source_aliases: SourceAliasRules,
) -> None:
    assert _normalize("北京号客户端", source_aliases) == "北京日报"


def test_empty_result_after_suffix_stripping_preserves_original(
    source_aliases: SourceAliasRules,
) -> None:
    assert _normalize("客户端", source_aliases) == "客户端"


def test_alias_matching_requires_full_string_equality(
    source_aliases: SourceAliasRules,
) -> None:
    assert _normalize("南京号外北京号", source_aliases) == "南京号外北京号"


def test_unmatched_source_is_preserved(source_aliases: SourceAliasRules) -> None:
    assert _normalize("中国教育报", source_aliases) == "中国教育报"


@pytest.mark.parametrize("source", [None, "", " \t "])
def test_none_or_blank_source_is_safe_and_preserved(
    source: Optional[str],
    source_aliases: SourceAliasRules,
) -> None:
    assert _normalize(source, source_aliases) == source


def test_empty_rules_preserve_source() -> None:
    rules = SourceAliasRules()

    assert _normalize("北京号客户端", rules) == "北京号客户端"


def test_worker_normalizes_source_and_logs_changed_value(
    monkeypatch: pytest.MonkeyPatch,
    source_aliases: SourceAliasRules,
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
        source_aliases,
    )

    assert result.llm_source == "北京日报"
    assert messages == [
        (
            "enrich_summary",
            "NORMALIZED source name article_id=article-1 "
            "original='北京号客户端' normalized='北京日报'",
        )
    ]
