"""综报/晚报预览题头（标题 + 期号 + 日期）。

期号推算本身在 `issue_sequence`（纯函数）；本模块负责工作流上的三件事：

1. **确定「报送日」锚点**。管理员 = 今天；值班编辑 = 所查看班次的
   `coverage_date`（班次结束日的上海日期，也是班次下拉里显示的日期）。该班次
   产出的报送稿正是在这一天报送的，所以两端口径一致，看历史班次不会算成今天。
2. **优先用归档**。这一天如果已经录了报送存档，直接返回那一期的真实期号与
   日期——历史班次显示的就是当时实际报送的题头，不依赖公式推断。
3. **没有归档才推算**。按「取材日 = 报送日（综报）/ 报送日 − 1（晚报）」，
   从最近一条可严格解析的归档按天数差推算。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

from src.adapters.db_postgres_core import get_adapter
from src.console.auth_service import ConsoleUser
from src.console.shifts_service import require_owned_shift

from . import issue_sequence

_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
# 基准只在最近的存档里找：期号必须近在眼前，往回翻太多没有意义。
_BASELINE_SCAN_LIMIT = 20


def business_today() -> date:
    return datetime.now(_BUSINESS_TIMEZONE).date()


def _issue_date(report_type: str, business_date: date) -> date:
    """该报送日对应的「取材日」：晚报报送的是前一天的取材日。"""
    if report_type == "wanbao":
        return business_date - timedelta(days=1)
    return business_date


def _match_report(
    rows: list[dict[str, Any]],
    report_type: str,
) -> Optional[issue_sequence.IssueBaseline]:
    """在给定行里挑出第一条标题匹配、期号可严格解析的归档。"""
    expected_title = issue_sequence.TITLE_LINES[report_type]
    for row in rows:
        # 标题必须逐字一致：验收测试、反馈稿等脏数据不参与基准。
        if str(row.get("title_line") or "").strip() != expected_title:
            continue
        numbers = issue_sequence.parse_issue_no(
            report_type, row.get("issue_no")
        )
        if numbers is None:
            continue
        report_date = row["report_date"]
        compiled_date = row.get("compiled_date") or _issue_date(
            report_type, report_date
        )
        return issue_sequence.IssueBaseline(
            issue_no=str(row.get("issue_no") or "").strip(),
            report_date=report_date,
            compiled_date=compiled_date,
            numbers=numbers,
        )
    return None


def _fetch_reports(
    report_type: str,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = _BASELINE_SCAN_LIMIT,
) -> list[dict[str, Any]]:
    rows, _ = get_adapter().submission_archive.fetch_reports(
        report_type=report_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=0,
    )
    return rows


def _archived_for_date(
    report_type: str,
    report_date: date,
) -> Optional[issue_sequence.IssueBaseline]:
    """该报送日是否已有归档（同报别、同日、标题与期号都可解析）。"""
    rows = _fetch_reports(
        report_type,
        date_from=report_date,
        date_to=report_date,
        limit=10,
    )
    return _match_report(rows, report_type)


def _latest_baseline(
    report_type: str,
) -> Optional[issue_sequence.IssueBaseline]:
    return _match_report(_fetch_reports(report_type), report_type)


def _unavailable(report_type: str) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "available": False,
        "lines": [],
        "title_line": "",
        "issue_line": "",
        "date_line": "",
        "business_date": None,
        "issue_date": None,
        "source": None,
        "baseline": None,
        "day_offset": None,
        "notices": ["尚未录入该报别的报送存档，无法推算期号"],
    }


def _payload(
    report_type: str,
    *,
    lines: tuple[str, str, str],
    business_date: date,
    issue_date: Optional[date],
    source: str,
    baseline: Optional[issue_sequence.IssueBaseline],
    day_offset: Optional[int],
) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "available": True,
        "lines": list(lines),
        "title_line": lines[0],
        "issue_line": lines[1],
        "date_line": lines[2],
        "business_date": business_date,
        "issue_date": issue_date,
        "source": source,
        "baseline": (
            None
            if baseline is None
            else {
                "report_date": baseline.report_date,
                "compiled_date": baseline.compiled_date,
                "issue_no": baseline.issue_no,
            }
        ),
        "day_offset": day_offset,
        "notices": [],
    }


def build_preview_header(
    *,
    report_type: str,
    business_date: Optional[date] = None,
) -> dict[str, Any]:
    """按「报送日」返回预览题头三行；该日报送稿已归档时直接取那一期。"""
    if report_type not in issue_sequence.TITLE_LINES:
        raise issue_sequence.UnknownReportTypeError(
            f"不支持的报别: {report_type}"
        )
    target = business_date or business_today()

    archived = _archived_for_date(report_type, target)
    if archived is not None:
        # 期号行直接用归档里的原文，不重新格式化，避免历史格式差异
        return _payload(
            report_type,
            lines=(
                issue_sequence.TITLE_LINES[report_type],
                archived.issue_no,
                issue_sequence.format_date_line(target),
            ),
            business_date=target,
            issue_date=archived.compiled_date,
            source="archived",
            baseline=archived,
            day_offset=None,
        )

    baseline = _latest_baseline(report_type)
    if baseline is None:
        return _unavailable(report_type)

    issue_date = _issue_date(report_type, target)
    numbers, day_offset = issue_sequence.next_issue_numbers(
        baseline=baseline,
        target_date=issue_date,
    )
    return _payload(
        report_type,
        lines=issue_sequence.header_lines(report_type, numbers, target),
        business_date=target,
        issue_date=issue_date,
        source="computed",
        baseline=baseline,
        day_offset=day_offset,
    )


def build_shift_preview_header(
    *,
    shift_id: str,
    user: ConsoleUser,
    report_type: str,
) -> dict[str, Any]:
    """值班编辑视角：以所查看班次的 coverage_date 作为报送日。"""
    shift = require_owned_shift(shift_id, user, allow_cancelled=True)
    return build_preview_header(
        report_type=report_type,
        business_date=shift["coverage_date"],
    )


__all__ = [
    "build_preview_header",
    "build_shift_preview_header",
    "business_today",
]
