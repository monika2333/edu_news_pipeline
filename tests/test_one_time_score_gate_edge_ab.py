from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import one_time_score_gate_edge_ab as analysis


def _row(**overrides):
    row = {
        "article_id": "a1",
        "title": "普通标题",
        "source": "北京日报",
        "publish_time_iso": None,
        "url": "https://example.test/a1",
        "content_markdown": "普通正文",
        "score": 30,
        "score_details": {"matched_rules": []},
    }
    row.update(overrides)
    return row


def test_queries_are_read_only() -> None:
    forbidden = (" update ", " insert ", " delete ", " create ", " alter ", " drop ")
    for query in analysis.READ_QUERIES:
        normalized = f" {' '.join(query.lower().split())} "
        assert normalized.lstrip().startswith(("select ", "with "))
        assert not any(token in normalized for token in forbidden)


def test_category_match_excludes_source() -> None:
    candidate = analysis._build_candidate(_row(), {"北京"})
    assert candidate.source == "北京日报"
    assert candidate.is_beijing_related is False
    assert candidate.candidate_category == "external_positive"
    assert candidate.summary == ""


def test_pair_calls_only_differ_by_content_limit() -> None:
    calls = []

    def fake_call(candidate, **kwargs):
        calls.append((candidate, kwargs))
        return "40" if kwargs["content_limit"] == 1500 else "55"

    with patch.object(analysis, "call_external_filter_model", side_effect=fake_call), patch.object(
        analysis,
        "parse_external_filter_score",
        side_effect=lambda raw: int(raw),
    ):
        result, quota_halt = analysis._score_pair(_row(), {"北京"}, retries=2)

    assert quota_halt is False
    assert result.score_a == 40
    assert result.score_b == 55
    assert calls[0][0] is calls[1][0]
    first = dict(calls[0][1])
    second = dict(calls[1][1])
    assert first.pop("content_limit") == 1500
    assert second.pop("content_limit") == 4000
    assert first == second == {"category": "external_positive", "retries": 2}


def test_parse_log_counts_matches_run_id(tmp_path: Path) -> None:
    (tmp_path / "pipeline.log").write_text(
        "[score] result: ok=12 failed=1\n"
        "[enrich_summary] result: ok=8 failed=0\n"
        "run_id: abc123\n",
        encoding="utf-8",
    )
    counts = analysis._parse_log_counts(tmp_path, {"abc123"})
    assert counts[("abc123", "score")] == 13
    assert counts[("abc123", "enrich-summary")] == 8


def test_parse_log_counts_supports_utf16_pipeline_logs(tmp_path: Path) -> None:
    (tmp_path / "pipeline_utf16.log").write_text(
        "[summarize] result: ok=7 failed=0\nrun_id: def456\n",
        encoding="utf-16",
    )
    counts = analysis._parse_log_counts(tmp_path, {"def456"})
    assert counts[("def456", "summarize")] == 7


def test_threshold_and_length_summaries() -> None:
    results = [
        analysis.ABResult("a", "t", "u", 30, 30, 40, "external_positive", 1000),
        analysis.ABResult("b", "t", "u", 30, 60, 50, "internal_positive", 3000),
    ]
    threshold_35 = analysis._threshold_rows(results)[2]
    assert threshold_35[1] == "1 (50.00%)"
    assert threshold_35[2] == "2 (100.00%)"
    assert threshold_35[3:] == (1, 0, 1)
    assert analysis._length_group(1499) == "<1500"
    assert analysis._length_group(1500) == "1500-4000"
    assert analysis._length_group(4001) == ">4000"


def test_linear_fit_separates_fixed_and_variable_cost() -> None:
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    points = [
        analysis.DedupRunPoint("a", now, 30.0, 10, 1, 100.0),
        analysis.DedupRunPoint("b", now, 50.0, 20, 2, 120.0),
        analysis.DedupRunPoint("c", now, 70.0, 30, 3, 140.0),
    ]
    fit = analysis._linear_fit(points)
    assert fit is not None
    assert fit.intercept_seconds == 10.0
    assert fit.slope_seconds_per_article == 2.0
    assert fit.r_squared == 1.0


def test_write_csv_has_requested_columns(tmp_path: Path) -> None:
    path = tmp_path / "details.csv"
    analysis.write_csv(
        path,
        [analysis.ABResult("a", "标题", "url", 30, 40, 50, "external_positive", 2000)],
    )
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header.startswith(
        "article_id,title,url,relevance_score,score_a_1500,score_b_4000,category,content_chars,b_higher_than_a"
    )
    restored = analysis.read_csv(path)
    assert restored["a"].score_a == 40
    assert restored["a"].score_b == 50
