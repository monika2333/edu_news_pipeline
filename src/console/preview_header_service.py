"""综报/晚报预览题头（标题 + 期号 + 日期）。

期号推算本身在 `issue_sequence`（纯函数）；本模块只负责三件工作流上的事：

1. 从报送存档里找「最近一期」当基准（严格解析，跳过验收测试数据）；
2. 按角色决定预览的「目标取材日」——综报当天报送，晚报第二天早上报送；
3. 组织成前端可直接渲染的题头三行 + 提示。

时间口径固定按 Asia/Shanghai 取「今天」。管理员只在报送当天早上点预览，所以
「今天」就是报送日；值班编辑晚上做稿，「今天」就是制作日（= 取材日）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from src.adapters.db_postgres_core import get_adapter
from src.domain.submission_archive_parser import default_compiled_date

from . import issue_sequence

_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
# 基准只在最近的存档里找：期号必须近在眼前，往回翻太多没有意义。
_BASELINE_SCAN_LIMIT = 20
# 基准超过这么多天没更新，说明存档漏录了，提示一句但不阻断。
_STALE_BASELINE_DAYS = 7

Viewer = Literal["admin", "duty"]


def business_today() -> date:
    return datetime.now(_BUSINESS_TIMEZONE).date()


def _target_issue_date(report_type: str, viewer: Viewer, today: date) -> date:
    """预览时用于推算期号的「目标取材日」。

    晚报的日期行是报送日，取材日是它的前一天；值班编辑写的是制作日
    （等于取材日），管理员写的是报送日，所以管理员端要往前退一天。
    """
    if report_type == "wanbao" and viewer == "admin":
        return today - timedelta(days=1)
    return today


def _fetch_baseline(
    report_type: str,
) -> Optional[issue_sequence.IssueBaseline]:
    expected_title = issue_sequence.TITLE_LINES[report_type]
    rows, _ = get_adapter().submission_archive.fetch_reports(
        report_type=report_type,
        date_from=None,
        date_to=None,
        limit=_BASELINE_SCAN_LIMIT,
        offset=0,
    )
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
        compiled_date = row.get("compiled_date") or default_compiled_date(
            report_type, report_date
        )
        return issue_sequence.IssueBaseline(
            issue_no=str(row.get("issue_no") or "").strip(),
            report_date=report_date,
            compiled_date=compiled_date,
            numbers=numbers,
        )
    return None


def _unavailable(report_type: str) -> dict[str, Any]:
    return {
        "report_type": report_type,
        "available": False,
        "lines": [],
        "title_line": "",
        "issue_line": "",
        "date_line": "",
        "issue_date": None,
        "report_date": None,
        "day_offset": None,
        "baseline": None,
        "notices": ["尚未录入该报别的报送存档，无法推算期号"],
    }


def build_preview_header(
    *,
    report_type: str,
    viewer: Viewer,
    today: Optional[date] = None,
) -> dict[str, Any]:
    """返回预览题头三行与推算依据。基准缺失时 `available` 为 False。"""
    if report_type not in issue_sequence.TITLE_LINES:
        raise issue_sequence.UnknownReportTypeError(
            f"不支持的报别: {report_type}"
        )
    current = today or business_today()
    target_date = _target_issue_date(report_type, viewer, current)
    baseline = _fetch_baseline(report_type)
    if baseline is None:
        return _unavailable(report_type)

    numbers, day_offset = issue_sequence.next_issue_numbers(
        baseline=baseline,
        target_date=target_date,
    )
    # 日期行固定写「今天」：管理员写的是报送日，值班编辑写的是制作日，
    # 两者都是当天。期号轴（target_date）才按角色差一天。
    lines = issue_sequence.header_lines(report_type, numbers, current)

    notices: list[str] = []
    if day_offset == 0:
        notices.append(
            f"本期与已归档的 {baseline.issue_no}"
            f"（{baseline.compiled_date:%Y-%m-%d}）是同一期，如已报送可忽略"
        )
    stale_days = (current - baseline.compiled_date).days
    if stale_days > _STALE_BASELINE_DAYS:
        notices.append(
            f"期号依据为 {baseline.compiled_date:%Y-%m-%d} 的"
            f" {baseline.issue_no}，已 {stale_days} 天未录入新存档"
        )

    return {
        "report_type": report_type,
        "available": True,
        "lines": list(lines),
        "title_line": lines[0],
        "issue_line": lines[1],
        "date_line": lines[2],
        "issue_date": target_date,
        "report_date": current,
        "day_offset": day_offset,
        "baseline": {
            "report_date": baseline.report_date,
            "compiled_date": baseline.compiled_date,
            "issue_no": baseline.issue_no,
        },
        "notices": notices,
    }


__all__ = ["Viewer", "build_preview_header", "business_today"]
