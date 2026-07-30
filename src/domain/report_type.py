"""报别（综报/晚报）与报送稿类型的权威定义。

本模块是这两个枚举在项目中的唯一定义源。其他模块应从此处导入，
不要重新声明字面量集合。
"""
from __future__ import annotations

from typing import Literal, Optional

# ── 新闻报别 ────────────────────────────────────────────────────────────
# 一条待报送的新闻会被分流到综报或晚报。这里不包含 feedback：
# 反馈是一种"已报送文档"的类型，新闻不会被分流到反馈。
#
# ORDER 是有序元组，用于 CLI choices 和界面展示顺序；集合用于成员校验。
NEWS_REPORT_TYPE_ORDER: tuple[str, ...] = ("zongbao", "wanbao")
NEWS_REPORT_TYPES = frozenset(NEWS_REPORT_TYPE_ORDER)
NewsReportType = Literal["zongbao", "wanbao"]

DEFAULT_REPORT_TYPE = "zongbao"

# ── 报送稿类型 ──────────────────────────────────────────────────────────
# 一份已经报送出去的文档，除综报/晚报外还可能是一份反馈。
SUBMISSION_DOC_TYPES = frozenset({"zongbao", "wanbao", "feedback"})
SubmissionDocType = Literal["zongbao", "wanbao", "feedback"]


def normalize_report_type(report_type: Optional[str]) -> Optional[str]:
    """过滤语义的归一化。

    空值 → None，表示"不按报别过滤"，调用方应据此跳过 WHERE 条件。
    合法值 → 原值（已小写去空格）。
    无法识别的值 → DEFAULT_REPORT_TYPE（静默容错，不抛异常）。

    注意：不要把本函数与 coerce_report_type 合并。二者对空值的
    处理语义相反，混用会导致 SQL 查询被意外加上报别过滤条件。
    """
    value = (report_type or "").strip().lower()
    if not value:
        return None
    if value in NEWS_REPORT_TYPES:
        return value
    return DEFAULT_REPORT_TYPE


def coerce_report_type(report_type: Optional[str]) -> str:
    """默认语义的归一化。

    空值或无法识别的值 → DEFAULT_REPORT_TYPE。一定返回合法报别。
    用于决定一条新闻归属哪个报别桶。

    注意：不要把本函数与 normalize_report_type 合并，理由同上。
    """
    value = (report_type or DEFAULT_REPORT_TYPE).strip().lower()
    return value if value in NEWS_REPORT_TYPES else DEFAULT_REPORT_TYPE


__all__ = [
    "DEFAULT_REPORT_TYPE",
    "NEWS_REPORT_TYPES",
    "NEWS_REPORT_TYPE_ORDER",
    "NewsReportType",
    "SUBMISSION_DOC_TYPES",
    "SubmissionDocType",
    "coerce_report_type",
    "normalize_report_type",
]
