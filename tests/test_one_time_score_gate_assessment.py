from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import one_time_score_gate_assessment as assessment


def test_analysis_queries_are_select_only() -> None:
    forbidden = (" update ", " insert ", " delete ", " create ", " alter ", " drop ")

    for query in assessment.READ_QUERIES:
        normalized = f" {' '.join(query.lower().split())} "
        assert normalized.lstrip().startswith(("select ", "with "))
        assert not any(token in normalized for token in forbidden)


def test_score_band_handles_boundaries_and_null() -> None:
    assert assessment._score_band(None) == "NULL"
    assert assessment._score_band(0) == "0-9"
    assert assessment._score_band(9.999) == "0-9"
    assert assessment._score_band(10) == "10-19"
    assert assessment._score_band(59) == "50-59"
    assert assessment._score_band(100) == "90-100"
    assert assessment._display_bands({"40-49": 2}) == list(
        assessment.STANDARD_SCORE_BANDS
    )


def test_sample_scoring_reuses_external_filter_adapter_with_no_summary() -> None:
    row = {
        "article_id": "article-1",
        "title": "北京高校发布新规",
        "source": "测试来源",
        "publish_time_iso": None,
        "url": "https://example.test/1",
        "content_markdown": "北京高校正文",
        "score": 55,
        "score_details": {"matched_rules": [{"label": "北京高校"}]},
    }

    with patch.object(
        assessment,
        "call_external_filter_model",
        return_value="评分：72",
    ) as call, patch.object(
        assessment,
        "parse_external_filter_score",
        return_value=72,
    ) as parse:
        result = assessment._score_sample_row(row, {"北京"}, retries=2)

    candidate = call.call_args.args[0]
    assert candidate.summary == ""
    assert candidate.sentiment_label == "positive"
    assert candidate.is_beijing_related is True
    assert candidate.keyword_matches == ("北京高校",)
    assert call.call_args.kwargs == {"category": "internal_positive", "retries": 2}
    parse.assert_called_once_with("评分：72")
    assert result.rubric_score == 72
    assert result.category == "internal_positive"


def test_report_states_sample_assumptions_and_thresholds(tmp_path: Path) -> None:
    data = assessment.AssessmentData(
        primary_statuses=Counter(
            {"scored": 60, "filtered_out": 30, "failed": 5, "other": 5}
        ),
        other_statuses=Counter({"pending": 5}),
        filtered_histogram=Counter({"40-49": 10, "50-59": 20}),
        external_distribution={
            "external_positive": {
                "10-19": Counter({"external_filtered": 4}),
                "20-29": Counter({"ready_for_export": 6}),
            }
        },
        sampled_count=2,
        sample_results=[
            assessment.LeakSampleResult("a1", "标题1", "u1", 50, 75, "internal_positive"),
            assessment.LeakSampleResult("a2", "标题2", "u2", 40, 55, "external_positive"),
        ],
    )
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)

    report = assessment.render_report(
        data,
        generated_at=now,
        since=now,
        days=14,
        requested_sample_size=200,
        seed="seed",
        model_name="model-name",
        beijing_keyword_count=12,
        csv_path=tmp_path / "details.csv",
    )

    assert "请求抽样规模：200" in report
    assert "无摘要前提" in report
    assert "统一按 positive" in report
    assert "本次加载 12 个词" in report
    assert "internal_positive / external_positive" in report
    assert "rubric >= 70" in report
    assert "rubric >= 50" in report
    assert "ready_for_export" in report
    assert "external_filtered" in report


def test_write_csv_includes_requested_detail_columns(tmp_path: Path) -> None:
    path = tmp_path / "details.csv"
    assessment.write_csv(
        path,
        [assessment.LeakSampleResult("a1", "标题", "url", 55, 80, "external_positive")],
    )

    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header == (
        "article_id,title,url,relevance_score,rubric_score,category,error"
    )
