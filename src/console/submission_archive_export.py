from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import quote


MAX_EXPORT_ROWS = 50_000
CSV_HEADERS = (
    "报别",
    "报送日期",
    "取材日期",
    "期号",
    "序号",
    "章节",
    "标题",
    "正文",
    "来源",
    "原文链接",
)
REPORT_TYPE_LABELS = {
    "zongbao": "综报",
    "wanbao": "晚报",
    "feedback": "反馈",
}


def _date_text(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value)


def _normalize_body(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n")


def _join_urls(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence):
        return " ".join(str(url) for url in value)
    return str(value)


def build_csv_bytes(rows: Sequence[Mapping[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)
    for row in rows:
        writer.writerow(
            (
                REPORT_TYPE_LABELS[str(row["report_type"])],
                _date_text(row.get("report_date")),
                _date_text(row.get("compiled_date")),
                "" if row.get("issue_no") is None else row["issue_no"],
                "" if row.get("order_index") is None else row["order_index"],
                "" if row.get("section") is None else row["section"],
                "" if row.get("title") is None else row["title"],
                _normalize_body(row.get("body")),
                "" if row.get("source") is None else row["source"],
                _join_urls(row.get("urls")),
            )
        )
    return output.getvalue().encode("utf-8-sig")


def build_export_filenames(
    date_from: Optional[date],
    date_to: Optional[date],
) -> tuple[str, str]:
    start = date_from.strftime("%Y%m%d") if date_from else ""
    end = date_to.strftime("%Y%m%d") if date_to else ""
    if not start and not end:
        return "报送存档_全部.csv", "submission_archive_all.csv"
    date_range = f"{start}-{end}"
    return f"报送存档_{date_range}.csv", f"submission_archive_{date_range}.csv"


def build_content_disposition(
    date_from: Optional[date],
    date_to: Optional[date],
) -> str:
    utf8_name, ascii_name = build_export_filenames(date_from, date_to)
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(utf8_name)}"
    )


__all__ = [
    "CSV_HEADERS",
    "MAX_EXPORT_ROWS",
    "REPORT_TYPE_LABELS",
    "build_content_disposition",
    "build_csv_bytes",
    "build_export_filenames",
]
