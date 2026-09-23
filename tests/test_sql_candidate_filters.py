"""细化筛选 SQL 子句构造器与参数归一化的单元测试。

`candidate_extra_filter_clauses` 是管理员候选列表与值班候选列表共用的唯一
过滤实现（manual_reviews 与 shift_reviews 两个 adapter 都引用它），语义回归
必须锁在这里；子句按 `ns` 别名书写（两条查询链中 news_summaries 的别名）。
"""
from __future__ import annotations

import pytest

from src.adapters.sql_candidate_filters import (
    DUPLICATE_TAGGED_SQL,
    candidate_created_hour_expr,
    candidate_extra_filter_clauses,
)
from src.console.manual_filter_helpers import normalize_candidate_refine_filters


def clauses_of(**kwargs):
    return candidate_extra_filter_clauses(**kwargs)[0]


def params_of(**kwargs):
    return candidate_extra_filter_clauses(**kwargs)[1]


def test_no_filters_yields_no_clauses():
    assert clauses_of() == []
    assert params_of() == []


def test_hour_range_generates_closed_interval():
    clauses = clauses_of(hour_from=8, hour_to=12)
    hour = candidate_created_hour_expr()
    assert clauses == [f"{hour} >= %s", f"{hour} <= %s"]
    assert params_of(hour_from=8, hour_to=12) == [8, 12]


def test_single_hour_uses_equality():
    clauses = clauses_of(hour_from=8, hour_to=8)
    assert clauses == [f"{candidate_created_hour_expr()} = %s"]
    assert params_of(hour_from=8, hour_to=8) == [8]


def test_wraparound_hour_uses_or_group():
    clauses = clauses_of(hour_from=22, hour_to=6)
    hour = candidate_created_hour_expr()
    assert clauses == [f"({hour} >= %s OR {hour} <= %s)"]
    assert params_of(hour_from=22, hour_to=6) == [22, 6]


def test_open_ended_hour_bounds():
    assert clauses_of(hour_from=8) == [f"{candidate_created_hour_expr()} >= %s"]
    assert clauses_of(hour_to=6) == [f"{candidate_created_hour_expr()} <= %s"]


def test_duplicate_state_filters_by_non_dismissed_exists():
    untagged = clauses_of(duplicate_state="untagged")
    tagged = clauses_of(duplicate_state="tagged")
    assert untagged == [f"NOT {DUPLICATE_TAGGED_SQL}"]
    assert tagged == [DUPLICATE_TAGGED_SQL]
    # 与 fetch_duplicate_badges 的徽章口径一致：dismissed 不算带标签
    assert "sdm.state <> 'dismissed'" in DUPLICATE_TAGGED_SQL
    assert "sdm.article_id = ns.article_id" in DUPLICATE_TAGGED_SQL


def test_score_bounds_are_inclusive():
    clauses = clauses_of(min_score=60, max_score=90)
    assert clauses == [
        "ns.external_importance_score >= %s",
        "ns.external_importance_score <= %s",
    ]
    assert params_of(min_score=60, max_score=90) == [60, 90]


def test_combined_filters_keep_order():
    clauses = clauses_of(
        hour_from=8,
        duplicate_state="untagged",
        min_score=60,
    )
    assert len(clauses) == 3
    assert clauses[0].endswith(">= %s")
    assert clauses[1].startswith("NOT EXISTS")
    assert clauses[2] == "ns.external_importance_score >= %s"
    assert params_of(hour_from=8, duplicate_state="untagged", min_score=60) == [8, 60]


class TestNormalizeCandidateRefineFilters:
    def test_defaults_pass_through_as_none(self):
        normalized = normalize_candidate_refine_filters()
        assert normalized == {
            "hour_from": None,
            "hour_to": None,
            "duplicate_state": None,
            "min_score": None,
            "max_score": None,
        }

    def test_valid_values_are_coerced(self):
        normalized = normalize_candidate_refine_filters(
            hour_from="8",
            hour_to=23,
            duplicate_state="untagged",
            min_score="60.5",
            max_score=100,
        )
        assert normalized == {
            "hour_from": 8,
            "hour_to": 23,
            "duplicate_state": "untagged",
            "min_score": 60.5,
            "max_score": 100.0,
        }

    def test_all_duplicate_state_is_none(self):
        assert normalize_candidate_refine_filters(duplicate_state="all")[
            "duplicate_state"
        ] is None
        assert normalize_candidate_refine_filters(duplicate_state="")[
            "duplicate_state"
        ] is None

    @pytest.mark.parametrize("value", [24, -1, "abc", 7.5])
    def test_invalid_hour_rejected(self, value):
        with pytest.raises(ValueError, match="hour_from"):
            normalize_candidate_refine_filters(hour_from=value)

    @pytest.mark.parametrize("value", ["high", object()])
    def test_invalid_score_rejected(self, value):
        with pytest.raises(ValueError, match="min_score"):
            normalize_candidate_refine_filters(min_score=value)

    def test_invalid_duplicate_state_rejected(self):
        with pytest.raises(ValueError, match="duplicate_state"):
            normalize_candidate_refine_filters(duplicate_state="tagged_only")

    def test_min_score_above_max_score_rejected(self):
        with pytest.raises(ValueError, match="min_score"):
            normalize_candidate_refine_filters(min_score=80, max_score=60)
