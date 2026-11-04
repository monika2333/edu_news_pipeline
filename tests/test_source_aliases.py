from __future__ import annotations

from typing import Optional

import pytest

from src.domain import SourceAliasRules, matches_fallback_name, normalize_source_name
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


# ---------------------------------------------------------------------------
# 回退名单：整串全等命中即回退抓取来源（纯规则，与 worker 无关）
# ---------------------------------------------------------------------------


def test_fallback_name_hits_exact_match() -> None:
    rules = SourceAliasRules(fallback_names=("现代教育报",))

    assert matches_fallback_name("现代教育报", "现代教育报", rules) is True


@pytest.mark.parametrize(
    "name",
    ["现代教育报社", "《现代教育报》", "现代教育报客户端"],
)
def test_fallback_name_requires_full_string_equality(name: str) -> None:
    # 名单只做精准匹配：机构名派生、书名号包裹、带渠道后缀都不命中，
    # 大小写与符号一律不做归一。包含匹配类变异在此被抓出。
    rules = SourceAliasRules(fallback_names=("现代教育报",))

    assert matches_fallback_name(name, name, rules) is False


def test_fallback_name_matches_normalized_name() -> None:
    # 名单写标准名称：识别结果「现代教育报客户端」剥后缀后命中
    rules = SourceAliasRules(suffixes=("客户端",), fallback_names=("现代教育报",))

    assert matches_fallback_name("现代教育报客户端", "现代教育报", rules) is True


def test_fallback_name_matches_raw_name() -> None:
    # 名单写原始名称：归一化后的「现代教育报」不在名单，但归一化前命中
    rules = SourceAliasRules(suffixes=("客户端",), fallback_names=("现代教育报客户端",))

    assert matches_fallback_name("现代教育报客户端", "现代教育报", rules) is True


def test_empty_fallback_list_never_matches() -> None:
    # 名单为空时任何来源都不回退（M6 变异把字段改必填会破坏旧配置读取；
    # 把默认值改掉会在此暴露）
    assert matches_fallback_name("现代教育报", "现代教育报", SourceAliasRules()) is False
    assert (
        matches_fallback_name("现代教育报", "现代教育报", SourceAliasRules(fallback_names=()))
        is False
    )


def test_fallback_name_tolerates_none_inputs() -> None:
    rules = SourceAliasRules(fallback_names=("现代教育报",))

    assert matches_fallback_name(None, None, rules) is False
    assert matches_fallback_name(None, "现代教育报", rules) is True
    assert matches_fallback_name("现代教育报", None, rules) is True


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
