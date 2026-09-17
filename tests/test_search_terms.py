from __future__ import annotations

import pytest

from src.console.search_terms import (
    MAX_SEARCH_TERMS,
    TooManySearchTermsError,
    normalize_search_terms,
)


def test_splits_on_half_width_full_width_and_mixed_whitespace() -> None:
    assert normalize_search_terms("双减　课后服务\t课外\n培训 隔离") == [
        "双减",
        "课后服务",
        "课外",
        "培训",
        "隔离",
    ]


def test_collapses_consecutive_whitespace_and_strips_ends() -> None:
    assert normalize_search_terms("  双减   课后　　服务  ") == [
        "双减",
        "课后",
        "服务",
    ]


def test_dedupes_case_insensitively_keeping_first_spelling_and_order() -> None:
    assert normalize_search_terms("Java python JAVA javascript java Python") == [
        "Java",
        "python",
        "javascript",
    ]


def test_exactly_ten_terms_pass() -> None:
    terms = [f"词{i}" for i in range(MAX_SEARCH_TERMS)]
    assert normalize_search_terms(" ".join(terms)) == terms


def test_eleven_terms_raise_with_chinese_message() -> None:
    terms = [f"词{i}" for i in range(MAX_SEARCH_TERMS + 1)]
    with pytest.raises(TooManySearchTermsError) as exc_info:
        normalize_search_terms(" ".join(terms))
    assert "检索词最多" in str(exc_info.value)


def test_duplicate_terms_do_not_count_toward_the_cap() -> None:
    ten_distinct = [f"词{i}" for i in range(MAX_SEARCH_TERMS)]
    padded = [ten_distinct[0].upper(), *ten_distinct]
    assert normalize_search_terms(" ".join(padded)) == ten_distinct


def test_blank_and_whitespace_only_queries_return_empty_list() -> None:
    assert normalize_search_terms("") == []
    assert normalize_search_terms("   ") == []
    assert normalize_search_terms("　\t\n ") == []
    assert normalize_search_terms(None) == []  # type: ignore[arg-type]
