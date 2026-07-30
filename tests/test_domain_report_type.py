from __future__ import annotations

import pytest

from src.domain.report_type import (
    NEWS_REPORT_TYPES,
    NEWS_REPORT_TYPE_ORDER,
    SUBMISSION_DOC_TYPES,
    coerce_report_type,
    normalize_report_type,
)


@pytest.mark.parametrize("empty_value", [None, "", "   "])
def test_normalizers_preserve_distinct_empty_value_semantics(
    empty_value: str | None,
) -> None:
    # Filtering must keep an empty value as no filter; bucketing must default it.
    assert normalize_report_type(empty_value) is None
    assert coerce_report_type(empty_value) == "zongbao"


@pytest.mark.parametrize("report_type", ["wanbao", "WANBAO", " wanbao "])
def test_normalizers_accept_valid_values_case_insensitively(
    report_type: str,
) -> None:
    assert normalize_report_type(report_type) == "wanbao"
    assert coerce_report_type(report_type) == "wanbao"


def test_normalizers_silently_fall_back_for_invalid_values() -> None:
    assert normalize_report_type("xxx") == "zongbao"
    assert coerce_report_type("xxx") == "zongbao"


def test_news_and_submission_report_types_remain_distinct() -> None:
    # Feedback is a submitted document type, never a news-routing destination.
    assert NEWS_REPORT_TYPES == frozenset({"zongbao", "wanbao"})
    assert "feedback" not in NEWS_REPORT_TYPES
    assert "feedback" in SUBMISSION_DOC_TYPES


def test_news_report_type_order_is_stable_for_cli_choices() -> None:
    assert NEWS_REPORT_TYPE_ORDER == ("zongbao", "wanbao")
