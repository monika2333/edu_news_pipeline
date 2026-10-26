"""综报/晚报预览题头的期号解析、推算与格式化。

规则不是拍脑袋定的，而是从报送存档里的既有数据反推出来的：**期号是「取材日」
的线性函数**，每个报别每天 +1，中间空几天就跳几期。

- 综报：取材日 = 报送日（当天编辑当天报送），年度期号与总期号同步 +1；
- 晚报：取材日 = 报送日 − 1（`default_compiled_date` 已固化的口径），只有总期号。

由此，一次预览只需要「最近一期已归档的期号」这一个基准，加上目标取材日期，
就能算出任意一天的期号，不需要计数器、不需要谁先报送谁后报送；压稿到周一、
节假日停报、补报都自愈。年度期号跨年不归零，只换年份标签。

本模块只做纯计算，不碰数据库，也不决定「目标日期应该是哪天」——那是
`preview_header_service` 按角色决定的。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

from src.domain.report_type import NEWS_REPORT_TYPES
from src.domain.submission_archive_parser import SUPPORTED_SUBMISSION_TITLES

_ZONGBAO_ISSUE = re.compile(r"^(\d{4})年第(\d+)期（总第(\d+)期）$")
_WANBAO_ISSUE = re.compile(r"^总第(\d+)期$")

# 题头标题行必须与报送存档解析用的标题常量逐字一致，否则归档无法识别报别。
TITLE_LINES: dict[str, str] = {
    "zongbao": next(
        title for title in SUPPORTED_SUBMISSION_TITLES if "综报" in title
    ),
    "wanbao": next(
        title for title in SUPPORTED_SUBMISSION_TITLES if "综报" not in title
    ),
}


class UnknownReportTypeError(ValueError):
    """Raised when the report type is neither 综报 nor 晚报."""


@dataclass(frozen=True, slots=True)
class IssueNumbers:
    """一份报送稿的期号。晚报没有年度期号，`year_no` 为 None。"""

    total: int
    year_no: Optional[int] = None


@dataclass(frozen=True, slots=True)
class IssueBaseline:
    """推算基准：最近一期可严格解析的报送存档。"""

    issue_no: str
    report_date: date
    compiled_date: date
    numbers: IssueNumbers


def _require_report_type(report_type: str) -> None:
    if report_type not in NEWS_REPORT_TYPES:
        raise UnknownReportTypeError(f"不支持的报别: {report_type}")


def parse_issue_no(
    report_type: str,
    issue_no: Optional[str],
) -> Optional[IssueNumbers]:
    """严格解析归档里的期号；格式不符（例如验收测试数据）返回 None。

    严格解析是有意的：库里存在「总第9002期(验收测试)」这类记录，宽松匹配
    会把基准算到 9000 期以外。
    """
    _require_report_type(report_type)
    value = (issue_no or "").strip()
    if not value:
        return None
    if report_type == "zongbao":
        match = _ZONGBAO_ISSUE.match(value)
        if match is None:
            return None
        return IssueNumbers(
            total=int(match.group(3)),
            year_no=int(match.group(2)),
        )
    match = _WANBAO_ISSUE.match(value)
    if match is None:
        return None
    return IssueNumbers(total=int(match.group(1)))


def next_issue_numbers(
    *,
    baseline: IssueBaseline,
    target_date: date,
) -> tuple[IssueNumbers, int]:
    """按取材日推算目标日期的期号，返回 (期号, 天数差)。

    天数差可以为负：看历史班次时目标日比最近一条归档更早，需要往回推。
    （期号是取材日的线性函数，往前推与往后推是同一个公式。）
    """
    day_offset = (target_date - baseline.compiled_date).days
    numbers = IssueNumbers(
        total=baseline.numbers.total + day_offset,
        year_no=(
            None
            if baseline.numbers.year_no is None
            else baseline.numbers.year_no + day_offset
        ),
    )
    return numbers, day_offset


def format_issue_line(
    report_type: str,
    numbers: IssueNumbers,
    target_date: date,
) -> str:
    """格式化期号行。综报的年份标签取目标日期年份（跨年不归零）。"""
    _require_report_type(report_type)
    if report_type == "zongbao":
        if numbers.year_no is None:
            raise UnknownReportTypeError("综报期号缺少年度期号")
        return (
            f"{target_date.year}年第{numbers.year_no}期"
            f"（总第{numbers.total}期）"
        )
    return f"总第{numbers.total}期"


def format_date_line(target_date: date) -> str:
    return f"{target_date.year}年{target_date.month}月{target_date.day}日"


def header_lines(
    report_type: str,
    numbers: IssueNumbers,
    target_date: date,
) -> tuple[str, str, str]:
    """预览题头三行：标题、期号、日期。"""
    _require_report_type(report_type)
    return (
        TITLE_LINES[report_type],
        format_issue_line(report_type, numbers, target_date),
        format_date_line(target_date),
    )


__all__ = [
    "IssueBaseline",
    "IssueNumbers",
    "TITLE_LINES",
    "UnknownReportTypeError",
    "format_date_line",
    "format_issue_line",
    "header_lines",
    "next_issue_numbers",
    "parse_issue_no",
]
