from __future__ import annotations

from datetime import date
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException

from src.console import submission_archive_service
from src.console.security import ConsoleUser, require_console_user, require_role
from src.console.submission_archive_parser import SubmissionArchiveParseError
from src.console.submission_archive_schemas import (
    CreateSubmissionReportRequest,
    LinkDecisionRequest,
    ParseSubmissionReportRequest,
)
from src.workers.submission_archive_processing import (
    launch_submission_report_processing,
)

router = APIRouter(
    prefix="/api/submission-archive",
    tags=["submission_archive"],
)


def _raise_service_error(exc: Exception) -> NoReturn:
    if isinstance(
        exc,
        submission_archive_service.SubmissionReportConflictError,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "existing_report": exc.report,
            },
        ) from exc
    if isinstance(
        exc,
        submission_archive_service.SubmissionReportNotFoundError,
    ):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _schedule_report_processing(report_id: str) -> None:
    launch_submission_report_processing(report_id)


@router.post("/parse")
def parse_report_api(
    req: ParseSubmissionReportRequest,
    _user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        return submission_archive_service.parse_report(req.pasted_text)
    except SubmissionArchiveParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/reports")
def create_report_api(
    req: CreateSubmissionReportRequest,
    _user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        result = submission_archive_service.create_report(
            report_type=req.report_type,
            report_date=req.report_date,
            compiled_date=req.compiled_date,
            issue_no=req.issue_no,
            title_line=req.title_line,
            pasted_text=req.pasted_text,
            items=[item.model_dump() for item in req.items],
            overwrite=req.overwrite,
        )
    except (ValueError, RuntimeError) as exc:
        _raise_service_error(exc)
    _schedule_report_processing(str(result["report"]["id"]))
    return result


@router.get("/reports")
def list_reports_api(
    report_type: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    try:
        return submission_archive_service.list_reports(
            report_type=report_type,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        _raise_service_error(exc)


@router.get("/reports/{report_id}")
def get_report_api(report_id: str) -> dict[str, Any]:
    try:
        return submission_archive_service.get_report(report_id)
    except ValueError as exc:
        _raise_service_error(exc)


@router.delete("/reports/{report_id}")
def delete_report_api(
    report_id: str,
    _user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, bool]:
    try:
        submission_archive_service.delete_report(report_id)
    except ValueError as exc:
        _raise_service_error(exc)
    return {"deleted": True}


@router.post("/reports/{report_id}/reparse")
def reparse_report_api(
    report_id: str,
    _user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        result = submission_archive_service.reparse_report(report_id)
    except (ValueError, RuntimeError) as exc:
        _raise_service_error(exc)
    _schedule_report_processing(report_id)
    return result


@router.get("/link-queue")
def list_pending_links_api(
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    return submission_archive_service.list_pending_links(
        limit=limit,
        offset=offset,
    )


@router.post("/items/{item_id}/link-decision")
def decide_link_api(
    item_id: str,
    req: LinkDecisionRequest,
    user: ConsoleUser = Depends(require_console_user),
) -> dict[str, Any]:
    try:
        return submission_archive_service.decide_link(
            item_id=item_id,
            accepted=req.accepted,
            user=user,
        )
    except (ValueError, PermissionError) as exc:
        _raise_service_error(exc)


@router.get("/search")
def search_archive_api(
    q: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    return submission_archive_service.search_archive(query=q, limit=limit)


@router.post("/duplicates/{article_id}/dismiss")
def dismiss_duplicates_api(
    article_id: str,
    user: ConsoleUser = Depends(require_console_user),
) -> dict[str, int]:
    try:
        dismissed = submission_archive_service.dismiss_duplicates(
            article_id=article_id,
            user=user,
        )
    except (ValueError, PermissionError) as exc:
        _raise_service_error(exc)
    return {"dismissed": dismissed}


__all__ = ["router"]
