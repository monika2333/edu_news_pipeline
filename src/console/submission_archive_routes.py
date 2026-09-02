from __future__ import annotations

from datetime import date
from typing import Any, NoReturn, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from src.console import submission_archive_service
from src.console.security import ConsoleUser, require_console_user, require_role
from src.console.submission_archive_schemas import (
    CreateSubmissionReportRequest,
    LinkDecisionRequest,
    ManualLinkRequest,
    ParseSubmissionReportRequest,
    PriorItemMatchesResponse,
    UpdateSubmissionItemRequest,
)
from src.domain.submission_archive_parser import SubmissionArchiveParseError
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
        submission_archive_service.SubmissionLinkProcessingError,
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(
        exc,
        submission_archive_service.SubmissionReportConflictError,
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "existing_report": jsonable_encoder(exc.report),
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
    if result.get("created", True):
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


@router.get("/items/{item_id}/link-candidates")
def search_link_candidates_api(
    item_id: str,
    q: str = Query(..., min_length=1),
    window_days: int = Query(default=15, ge=0, le=3650),
    limit: int = Query(default=20, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Search news summaries around the submitted report's compiled date."""
    try:
        return submission_archive_service.search_link_candidates(
            item_id=item_id,
            query=q,
            window_days=window_days,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        _raise_service_error(exc)


@router.post("/items/{item_id}/manual-link")
def manual_link_item_api(
    item_id: str,
    req: ManualLinkRequest,
    user: ConsoleUser = Depends(require_console_user),
) -> dict[str, Any]:
    """Manually link a submitted report item to a news summary."""
    try:
        return submission_archive_service.manual_link_item(
            item_id=item_id,
            article_id=req.article_id,
            user=user,
        )
    except (ValueError, RuntimeError, PermissionError) as exc:
        _raise_service_error(exc)


@router.delete("/items/{item_id}/manual-link")
def manual_unlink_item_api(
    item_id: str,
    user: ConsoleUser = Depends(require_console_user),
) -> dict[str, Any]:
    """Remove a manual link from a submitted report item."""
    try:
        return submission_archive_service.manual_unlink_item(
            item_id=item_id,
            user=user,
        )
    except (ValueError, RuntimeError, PermissionError) as exc:
        _raise_service_error(exc)


@router.patch("/items/{item_id}")
def update_item_api(
    item_id: str,
    req: UpdateSubmissionItemRequest,
    _user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Edit stored text fields (title/body/source/urls) of a report item."""
    try:
        return submission_archive_service.update_item_fields(
            item_id=item_id,
            title=req.title,
            body=req.body,
            source=req.source,
            urls=req.urls,
        )
    except (ValueError, RuntimeError) as exc:
        _raise_service_error(exc)


@router.get("/search")
def search_archive_api(
    q: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    return submission_archive_service.search_archive(query=q, limit=limit)


@router.get("/duplicates/{article_id:path}")
def fetch_duplicate_details_api(article_id: str) -> dict[str, Any]:
    """返回某条新闻命中的已报送重复记录明细，含报送稿正文。"""
    try:
        return submission_archive_service.fetch_duplicate_details(article_id)
    except ValueError as exc:
        _raise_service_error(exc)


@router.get(
    "/items/{item_id}/prior-matches",
    response_model=PriorItemMatchesResponse,
)
def fetch_prior_item_match_details_api(
    item_id: str,
) -> dict[str, Any]:
    """Return prior zongbao/wanbao items matched to one feedback item."""
    try:
        return submission_archive_service.fetch_prior_item_match_details(item_id)
    except ValueError as exc:
        _raise_service_error(exc)


@router.post("/duplicates/{article_id:path}/dismiss")
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
