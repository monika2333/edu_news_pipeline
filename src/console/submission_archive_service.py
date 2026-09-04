from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlparse

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_submission_archive import PRIOR_MATCH_REPORT_TYPES
from src.console.auth_service import ConsoleUser
from src.domain.report_type import SUBMISSION_DOC_TYPES as VALID_REPORT_TYPES
from src.domain.submission_archive_parser import (
    normalize_submission_text,
    normalized_title_hash,
    parse_submission_report,
)
from src.workers import submission_archive_ingest


SubmissionReportConflictError = (
    submission_archive_ingest.SubmissionReportConflictError
)


class SubmissionReportNotFoundError(ValueError):
    """Raised when a submitted report or item does not exist."""


class SubmissionLinkProcessingError(RuntimeError):
    """Raised when the worker still owns a submission item."""


def _require_business_user_id(user: ConsoleUser) -> str:
    if not user.user_id:
        raise PermissionError("该操作需要数据库用户账号登录")
    return user.user_id


def _serialize_parse_result(pasted_text: str) -> dict[str, Any]:
    parsed = parse_submission_report(pasted_text)
    return {
        "title_line": parsed.title_line,
        "issue_no": parsed.issue_no,
        "report_date": parsed.report_date,
        "compiled_date": parsed.compiled_date,
        "detected_report_type": parsed.detected_report_type,
        "items": [asdict(item) for item in parsed.items],
        "warnings": parsed.warnings,
    }


def parse_report(pasted_text: str) -> dict[str, Any]:
    return _serialize_parse_result(pasted_text)


def create_report(
    *,
    report_type: str,
    report_date: date,
    compiled_date: date,
    issue_no: Optional[str],
    title_line: Optional[str],
    pasted_text: str,
    items: Sequence[Mapping[str, Any]],
    overwrite: bool,
) -> dict[str, Any]:
    return submission_archive_ingest.create_report(
        report_type=report_type,
        report_date=report_date,
        compiled_date=compiled_date,
        issue_no=issue_no,
        title_line=title_line,
        pasted_text=pasted_text,
        items=items,
        overwrite=overwrite,
        adapter=get_adapter(),
    )


def list_reports(
    *,
    report_type: Optional[str],
    date_from: Optional[date],
    date_to: Optional[date],
    limit: int,
    offset: int,
) -> dict[str, Any]:
    if report_type and report_type not in VALID_REPORT_TYPES:
        raise ValueError(f"不支持的报告类型: {report_type}")
    rows, total = get_adapter().submission_archive.fetch_reports(
        report_type=report_type,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return {
        "items": rows,
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def get_report(report_id: str) -> dict[str, Any]:
    report = get_adapter().submission_archive.fetch_report(report_id)
    if not report:
        raise SubmissionReportNotFoundError("未找到这份存档报告")
    report["prior_match_pending"] = (
        report.get("report_type") in PRIOR_MATCH_REPORT_TYPES
        and report.get("prior_match_completed_at") is None
    )
    return report


def list_pending_links(*, limit: int, offset: int) -> dict[str, Any]:
    rows, total = get_adapter().submission_archive.fetch_pending_links(
        limit=limit,
        offset=offset,
    )
    return {
        "items": rows,
        "total": total,
        "limit": max(1, min(limit, 200)),
        "offset": max(0, offset),
    }


def decide_link(
    *,
    item_id: str,
    accepted: bool,
    user: ConsoleUser,
) -> dict[str, Any]:
    updated = get_adapter().submission_archive.decide_link(
        item_id=item_id,
        accepted=accepted,
        actor_user_id=_require_business_user_id(user),
    )
    if not updated:
        raise SubmissionReportNotFoundError(
            "待确认条目不存在或已被其他人处理"
        )
    return updated


def decide_prior_match(
    *,
    item_id: str,
    decision: Optional[str],
    user: ConsoleUser,
) -> dict[str, Any]:
    normalized_item_id = (item_id or "").strip()
    if not normalized_item_id:
        raise ValueError("item_id 不能为空")
    normalized_decision: Optional[str] = None
    if decision is not None:
        normalized_decision = str(decision).strip()
        if normalized_decision not in {"submitted", "not_submitted"}:
            raise ValueError("不支持的已报送判定")

    result = get_adapter().submission_archive.set_item_prior_match_decision(
        item_id=normalized_item_id,
        decision=normalized_decision,
        actor_user_id=_require_business_user_id(user),
    )
    state = result.get("state")
    if state == "not_found":
        raise SubmissionReportNotFoundError("未找到这个存档条目")
    if state == "not_decidable":
        raise ValueError("该条目当前不可进行已报送人工判定")
    prior_match = result.get("prior_match")
    if state != "updated" or not isinstance(prior_match, dict):
        raise RuntimeError("已报送人工判定未返回结果")
    return {"item_id": normalized_item_id, "prior_match": prior_match}


def search_link_candidates(
    *,
    item_id: str,
    query: str,
    window_days: int,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        raise ValueError("q 去空白后不能为空")
    if not 0 <= window_days <= 3650:
        raise ValueError("window_days 必须在 0 到 3650 之间")
    if not 1 <= limit <= 50:
        raise ValueError("limit 必须在 1 到 50 之间")
    if offset < 0:
        raise ValueError("offset 不能小于 0")

    result = get_adapter().submission_archive.fetch_manual_link_candidates(
        item_id=item_id,
        query=normalized_query,
        window_days=window_days,
        limit=limit,
        offset=offset,
    )
    if not result:
        raise SubmissionReportNotFoundError("未找到这个存档条目")
    return {
        **result,
        "window_days": window_days,
    }


def _updated_manual_link_item(
    result: Mapping[str, Any],
) -> dict[str, Any]:
    state = result.get("state")
    if state == "not_found":
        raise SubmissionReportNotFoundError("未找到这个存档条目")
    if state == "processing":
        raise SubmissionLinkProcessingError(
            "条目正在自动回链处理中，请稍后再试"
        )
    if state == "article_not_found":
        raise ValueError("article_id 在 news_summaries 中不存在")
    item = result.get("item")
    if state != "updated" or not isinstance(item, dict):
        raise RuntimeError("人工回链更新未返回条目")
    return item


def manual_link_item(
    *,
    item_id: str,
    article_id: str,
    user: ConsoleUser,
) -> dict[str, Any]:
    normalized_article_id = (article_id or "").strip()
    if not normalized_article_id:
        raise ValueError("article_id 不能为空")
    result = get_adapter().submission_archive.manual_link_item(
        item_id=item_id,
        article_id=normalized_article_id,
        actor_user_id=_require_business_user_id(user),
    )
    return _updated_manual_link_item(result)


def manual_unlink_item(
    *,
    item_id: str,
    user: ConsoleUser,
) -> dict[str, Any]:
    result = get_adapter().submission_archive.manual_unlink_item(
        item_id=item_id,
        actor_user_id=_require_business_user_id(user),
    )
    return _updated_manual_link_item(result)


def update_item_fields(
    *,
    item_id: str,
    title: str,
    body: str,
    source: Optional[str],
    urls: Sequence[str],
) -> dict[str, Any]:
    normalized_title = (title or "").strip()
    if not normalized_title:
        raise ValueError("标题不能为空")
    normalized_body = (body or "").strip()
    normalized_urls = [
        str(url).strip()
        for url in urls or []
        if str(url).strip()
    ]
    invalid_urls = [
        url
        for url in normalized_urls
        if urlparse(url).scheme not in {"http", "https"}
    ]
    if invalid_urls:
        raise ValueError("包含非 HTTP(S) URL")
    result = get_adapter().submission_archive.update_item_fields(
        item_id=item_id,
        title=normalized_title,
        body=normalized_body,
        source=(source or "").strip() or None,
        urls=normalized_urls,
        norm_title=normalize_submission_text(normalized_title),
        norm_title_hash=normalized_title_hash(normalized_title),
    )
    state = result.get("state")
    if state == "not_found":
        raise SubmissionReportNotFoundError("未找到这个存档条目")
    if state == "processing":
        raise SubmissionLinkProcessingError(
            "条目正在自动回链处理中，请稍后再试"
        )
    item = result.get("item")
    if state != "updated" or not isinstance(item, dict):
        raise RuntimeError("条目更新未返回结果")
    return item


def search_archive(*, query: str, limit: int) -> dict[str, Any]:
    normalized_query = (query or "").strip()
    if not normalized_query:
        return {"items": [], "query": "", "total": 0}
    rows = get_adapter().submission_archive.search_report_items(
        query=normalized_query,
        limit=limit,
    )
    return {
        "items": rows,
        "query": normalized_query,
        "total": len(rows),
    }


def attach_duplicate_badges(
    items: list[dict[str, Any]],
    *,
    adapter: Any = None,
) -> None:
    article_ids = [str(item.get("article_id") or "") for item in items]
    target_adapter = adapter or get_adapter()
    badges = target_adapter.submission_archive.fetch_duplicate_badges(article_ids)
    for item in items:
        item["submission_duplicate"] = badges.get(
            str(item.get("article_id") or "")
        )


def fetch_duplicate_details(
    article_id: str,
    *,
    adapter: Any = None,
) -> dict[str, Any]:
    normalized_article_id = (article_id or "").strip()
    if not normalized_article_id:
        raise ValueError("article_id 不能为空")
    target_adapter = adapter or get_adapter()
    matches = target_adapter.submission_archive.fetch_duplicate_match_details(
        normalized_article_id
    )
    return {"matches": matches}


def fetch_prior_item_match_details(
    item_id: str,
    *,
    adapter: Any = None,
) -> dict[str, Any]:
    normalized_item_id = (item_id or "").strip()
    if not normalized_item_id:
        raise ValueError("item_id 不能为空")
    target_adapter = adapter or get_adapter()
    matches = (
        target_adapter.submission_archive.fetch_item_duplicate_match_details(
            normalized_item_id
        )
    )
    return {"matches": matches}


def dismiss_duplicates(
    *,
    article_id: str,
    user: ConsoleUser,
) -> int:
    normalized_article_id = (article_id or "").strip()
    if not normalized_article_id:
        raise ValueError("article_id 不能为空")
    return get_adapter().submission_archive.dismiss_duplicate_matches(
        article_id=normalized_article_id,
        actor_user_id=_require_business_user_id(user),
    )


__all__ = [
    "SubmissionLinkProcessingError",
    "SubmissionReportConflictError",
    "SubmissionReportNotFoundError",
    "attach_duplicate_badges",
    "create_report",
    "decide_link",
    "decide_prior_match",
    "dismiss_duplicates",
    "fetch_duplicate_details",
    "fetch_prior_item_match_details",
    "get_report",
    "list_pending_links",
    "list_reports",
    "manual_link_item",
    "manual_unlink_item",
    "parse_report",
    "search_archive",
    "search_link_candidates",
    "update_item_fields",
]
