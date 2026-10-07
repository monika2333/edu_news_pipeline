"""细化筛选 SQL 子句构造器与参数归一化的单元测试。

`candidate_extra_filter_clauses` 是管理员候选列表与值班候选列表共用的唯一
过滤实现（manual_reviews 与 shift_reviews 两个 adapter 都引用它），语义回归
必须锁在这里；子句按 `ns` 别名书写（两条查询链中 news_summaries 的别名）。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.adapters.sql_candidate_filters import (
    DUPLICATE_TAGGED_SQL,
    candidate_created_hour_expr,
    candidate_extra_filter_clauses,
    decided_at_filter_clauses,
)
from src.console.manual_filter_helpers import (
    ensure_bulk_restore_has_condition,
    has_discard_filters,
    normalize_candidate_refine_filters,
    normalize_discard_filters,
)


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


class TestDecidedAtFilterClauses:
    """放弃时间子句：列由调用方传入，两种条件都不命中 decided_at 为空的行。"""

    def test_decided_since_compares_shanghai_local_date_on_given_column(self):
        clauses, params = decided_at_filter_clauses(
            column="mr.decided_at",
            decided_since=date(2026, 10, 1),
        )

        assert clauses == [
            "(mr.decided_at AT TIME ZONE 'Asia/Shanghai')::date >= %s"
        ]
        assert params == [date(2026, 10, 1)]

    def test_decided_since_uses_caller_supplied_column(self):
        clauses, _ = decided_at_filter_clauses(
            column="sr.decided_at",
            decided_since=date(2026, 10, 1),
        )

        assert clauses == [
            "(sr.decided_at AT TIME ZONE 'Asia/Shanghai')::date >= %s"
        ]

    def test_batch_decided_at_uses_exact_equality(self):
        batch = datetime(2026, 10, 7, 6, 32, 5, 123456, tzinfo=timezone.utc)
        clauses, params = decided_at_filter_clauses(
            column="sr.decided_at",
            batch_decided_at=batch,
        )

        assert clauses == ["sr.decided_at = %s"]
        assert params == [batch]

    def test_empty_inputs_yield_no_clauses(self):
        assert decided_at_filter_clauses(column="mr.decided_at") == ([], [])

    def test_combined_conditions_keep_order(self):
        batch = datetime(2026, 10, 7, 6, 0, 0, tzinfo=timezone.utc)
        clauses, params = decided_at_filter_clauses(
            column="mr.decided_at",
            decided_since=date(2026, 10, 1),
            batch_decided_at=batch,
        )

        assert clauses == [
            "(mr.decided_at AT TIME ZONE 'Asia/Shanghai')::date >= %s",
            "mr.decided_at = %s",
        ]
        assert params == [date(2026, 10, 1), batch]


class TestNormalizeDiscardFilters:
    def test_defaults_are_all_none(self):
        assert normalize_discard_filters() == {
            "q": None,
            "region": None,
            "sentiment": None,
            "min_score": None,
            "max_score": None,
            "decided_since": None,
            "batch_decided_at": None,
        }

    def test_valid_values_are_normalized(self):
        normalized = normalize_discard_filters(
            region="internal",
            sentiment="negative",
            q="  教育政策  ",
            min_score="10.5",
            max_score=100,
            decided_since="2026-10-01",
            batch_decided_at="2026-10-07T06:32:05.123456Z",
        )

        assert normalized == {
            "q": "教育政策",
            "region": "internal",
            "sentiment": "negative",
            "min_score": 10.5,
            "max_score": 100.0,
            "decided_since": date(2026, 10, 1),
            "batch_decided_at": datetime(
                2026, 10, 7, 6, 32, 5, 123456, tzinfo=timezone.utc
            ),
        }

    def test_blank_region_and_sentiment_are_none(self):
        normalized = normalize_discard_filters(region="", sentiment="", q="   ")
        assert normalized["region"] is None
        assert normalized["sentiment"] is None
        assert normalized["q"] is None

    @pytest.mark.parametrize("value", ["beijing", "all", "internal_external"])
    def test_invalid_region_rejected(self, value):
        with pytest.raises(ValueError, match="region"):
            normalize_discard_filters(region=value)

    @pytest.mark.parametrize("value", ["neutral", "positive_only"])
    def test_invalid_sentiment_rejected(self, value):
        with pytest.raises(ValueError, match="sentiment"):
            normalize_discard_filters(sentiment=value)

    def test_min_score_above_max_score_rejected(self):
        with pytest.raises(ValueError, match="min_score"):
            normalize_discard_filters(min_score=80, max_score=60)

    def test_naive_batch_decided_at_rejected(self):
        with pytest.raises(ValueError, match="batch_decided_at"):
            normalize_discard_filters(
                batch_decided_at=datetime(2026, 10, 7, 14, 32, 5)
            )
        with pytest.raises(ValueError, match="batch_decided_at"):
            normalize_discard_filters(batch_decided_at="2026-10-07T14:32:05")

    def test_invalid_decided_since_rejected(self):
        with pytest.raises(ValueError, match="decided_since"):
            normalize_discard_filters(decided_since="10/01/2026")

    def test_has_discard_filters(self):
        assert not has_discard_filters(normalize_discard_filters())
        assert has_discard_filters({"q": "教育政策"})
        assert has_discard_filters({"batch_decided_at": object()})
        assert not has_discard_filters({"q": "", "region": None, "min_score": None})

    def test_ensure_bulk_restore_has_condition_raises_without_condition(self):
        with pytest.raises(ValueError, match="按条件恢复至少需要一个筛选条件"):
            ensure_bulk_restore_has_condition(normalize_discard_filters())

    def test_ensure_bulk_restore_has_condition_passes_with_condition(self):
        filters = normalize_discard_filters(region="internal")
        ensure_bulk_restore_has_condition(filters)
