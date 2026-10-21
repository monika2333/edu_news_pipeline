from __future__ import annotations

from datetime import date
from typing import Optional

import pytest

from src.console import issue_sequence


def _baseline(
    *,
    total: int,
    compiled: date,
    year_no: Optional[int] = None,
) -> issue_sequence.IssueBaseline:
    return issue_sequence.IssueBaseline(
        issue_no="总第0期",
        report_date=compiled,
        compiled_date=compiled,
        numbers=issue_sequence.IssueNumbers(total=total, year_no=year_no),
    )


def test_parse_zongbao_issue_extracts_both_numbers() -> None:
    numbers = issue_sequence.parse_issue_no(
        "zongbao", "2026年第573期（总第3851期）"
    )

    assert numbers == issue_sequence.IssueNumbers(total=3851, year_no=573)


def test_parse_wanbao_issue_extracts_total_only() -> None:
    numbers = issue_sequence.parse_issue_no("wanbao", "总第4847期")

    assert numbers == issue_sequence.IssueNumbers(total=4847)


@pytest.mark.parametrize(
    "report_type, issue_no",
    [
        # 验收测试数据带半角括号后缀，宽松匹配会把基准算到 9000 期以外
        ("wanbao", "总第9002期(验收测试)"),
        ("wanbao", "总第4764期（草稿）"),
        ("wanbao", ""),
        ("wanbao", None),
        ("zongbao", "总第3851期"),
        ("zongbao", "2026年第573期"),
    ],
)
def test_parse_rejects_noncanonical_issue_lines(
    report_type: str,
    issue_no: Optional[str],
) -> None:
    assert issue_sequence.parse_issue_no(report_type, issue_no) is None


def test_unknown_report_type_is_rejected() -> None:
    with pytest.raises(issue_sequence.UnknownReportTypeError):
        issue_sequence.parse_issue_no("feedback", "总第1期")


def test_next_issue_advances_one_per_day_across_weekend() -> None:
    baseline = _baseline(total=4842, compiled=date(2026, 10, 15))

    numbers, offset = issue_sequence.next_issue_numbers(
        baseline=baseline,
        target_date=date(2026, 10, 18),
    )

    assert numbers.total == 4845
    assert offset == 3


def test_next_issue_same_day_keeps_baseline_number() -> None:
    baseline = _baseline(total=4847, compiled=date(2026, 10, 20))

    numbers, offset = issue_sequence.next_issue_numbers(
        baseline=baseline,
        target_date=date(2026, 10, 20),
    )

    assert numbers.total == 4847
    assert offset == 0


def test_next_issue_never_goes_backwards() -> None:
    baseline = _baseline(total=4847, compiled=date(2026, 10, 20))

    numbers, offset = issue_sequence.next_issue_numbers(
        baseline=baseline,
        target_date=date(2026, 10, 1),
    )

    assert numbers.total == 4847
    assert offset == 0


def test_next_issue_year_number_continues_across_new_year() -> None:
    baseline = _baseline(
        total=3852,
        year_no=574,
        compiled=date(2026, 12, 30),
    )

    numbers, offset = issue_sequence.next_issue_numbers(
        baseline=baseline,
        target_date=date(2027, 1, 2),
    )

    assert offset == 3
    assert numbers.total == 3855
    assert numbers.year_no == 577
    assert (
        issue_sequence.format_issue_line("zongbao", numbers, date(2027, 1, 2))
        == "2027年第577期（总第3855期）"
    )


def test_next_issue_counts_leap_day() -> None:
    baseline = _baseline(total=100, compiled=date(2028, 2, 28))

    numbers, offset = issue_sequence.next_issue_numbers(
        baseline=baseline,
        target_date=date(2028, 3, 1),
    )

    assert offset == 2
    assert numbers.total == 102


def test_issue_and_date_lines_use_full_width_brackets() -> None:
    numbers = issue_sequence.IssueNumbers(total=3851, year_no=573)

    assert issue_sequence.format_issue_line(
        "zongbao", numbers, date(2026, 10, 20)
    ) == "2026年第573期（总第3851期）"
    assert issue_sequence.format_issue_line(
        "wanbao",
        issue_sequence.IssueNumbers(total=4847),
        date(2026, 10, 21),
    ) == "总第4847期"


def test_date_line_has_no_zero_padding() -> None:
    assert issue_sequence.format_date_line(date(2026, 9, 5)) == "2026年9月5日"
    assert issue_sequence.format_date_line(date(2026, 10, 21)) == "2026年10月21日"


def test_header_lines_reuse_archive_titles() -> None:
    lines = issue_sequence.header_lines(
        "zongbao",
        issue_sequence.IssueNumbers(total=3851, year_no=573),
        date(2026, 10, 20),
    )

    assert lines == (
        "首都教育每日舆情综报",
        "2026年第573期（总第3851期）",
        "2026年10月20日",
    )
    assert lines[0] == issue_sequence.TITLE_LINES["zongbao"]


def test_wanbao_header_lines() -> None:
    lines = issue_sequence.header_lines(
        "wanbao",
        issue_sequence.IssueNumbers(total=4847),
        date(2026, 10, 21),
    )

    assert lines == ("首都教育舆情", "总第4847期", "2026年10月21日")
