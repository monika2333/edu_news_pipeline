from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.console import articles_service
from src.console import exports_service
from src.console import runs_service

router = APIRouter(tags=["console"], include_in_schema=False)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "web_templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _parse_date(value: str | None) -> tuple[date | None, str | None]:
    if value is None:
        return None, None
    cleaned = value.strip()
    if not cleaned:
        return None, None
    try:
        parsed = datetime.strptime(cleaned, "%Y-%m-%d").date()
        return parsed, None
    except ValueError:
        return None, cleaned


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    limit: int = Query(10, ge=1, le=100),
    include_items: bool = Query(False),
    message: str | None = None,
    error: str | None = None,
) -> HTMLResponse:
    snapshot = runs_service.get_dashboard_snapshot(limit=limit)
    latest_export = snapshot["latest_export"]
    if include_items and latest_export:
        latest_export = exports_service.get_latest_export(include_items=True)
    context = {
        "request": request,
        "runs": snapshot["runs"],
        "latest_run": snapshot["latest_run"],
        "latest_export": latest_export,
        "include_items": include_items,
        "message": message,
        "error": error,
    }
    return templates.TemplateResponse("dashboard.html", context)


@router.post("/dashboard/trigger")
async def dashboard_trigger(
    request: Request,
    steps: str = Form(""),
    skip: str = Form(""),
    continue_on_error: bool = Form(False),
) -> RedirectResponse:
    parsed_steps = [item.strip() for item in steps.split(",") if item.strip()]
    parsed_skip = [item.strip() for item in skip.split(",") if item.strip()]
    try:
        result = runs_service.trigger_pipeline_run(
            steps=parsed_steps or None,
            skip=parsed_skip or None,
            continue_on_error=continue_on_error,
            trigger_source="console-web",
        )
        query = urlencode(
            {
                "message": f"Triggered run {result['run_id']}",
                "include_items": str(False).lower(),
            }
        )
    except ValueError as exc:
        query = urlencode(
            {
                "error": str(exc),
                "include_items": str(False).lower(),
            }
        )
    redirect_url = request.url_for("dashboard")
    if query:
        redirect_url = f"{redirect_url}?{query}"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/articles/search", response_class=HTMLResponse)
async def articles_search_page(
    request: Request,
    q: str | None = Query(None, min_length=1, max_length=200),
    source: str | None = Query(None),
    sentiment: str | None = Query(None),
    status: str | None = Query(None),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    page: int = Query(1, ge=1, le=200),
    limit: int = Query(20, ge=1, le=100),
) -> HTMLResponse:
    parsed_start, start_error = _parse_date(start_date)
    parsed_end, end_error = _parse_date(end_date)
    error_messages = []
    if start_error:
        error_messages.append(f"Start date must follow YYYY-MM-DD (got '{start_error}')")
    if end_error:
        error_messages.append(f"End date must follow YYYY-MM-DD (got '{end_error}')")

    if error_messages:
        result = {"items": [], "total": 0, "limit": limit, "page": page, "pages": 1}
    else:
        result = articles_service.search_articles(
            query=q,
            page=page,
            limit=limit,
            sources=[source] if source else None,
            sentiments=[sentiment] if sentiment else None,
            statuses=[status] if status else None,
            start_date=parsed_start,
            end_date=parsed_end,
        )

    base_params = dict(request.query_params)

    def build_page_url(target: int) -> str:
        params = base_params.copy()
        params["page"] = str(target)
        params["limit"] = str(limit)
        encoded = urlencode(params)
        return f"{request.url.path}?{encoded}" if encoded else request.url.path

    has_prev = result["page"] > 1
    has_next = result["page"] < result["pages"]
    context = {
        "request": request,
        "query": q or "",
        "source": source or "",
        "sentiment": sentiment or "",
        "status": status or "",
        "start_date": start_date or "",
        "end_date": end_date or "",
        "limit": limit,
        "results": result["items"],
        "total": result["total"],
        "page": result["page"],
        "pages": result["pages"],
        "has_prev": has_prev,
        "has_next": has_next,
        "prev_url": build_page_url(result["page"] - 1) if has_prev else None,
        "next_url": build_page_url(result["page"] + 1) if has_next else None,
        "error": " ; ".join(error_messages) if error_messages else "",
    }
    return templates.TemplateResponse("search.html", context)


@router.get("/manual_filter", response_class=HTMLResponse)
async def manual_filter_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("manual_filter.html", {"request": request})


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def landing_page(request: Request) -> HTMLResponse:
    """根路径导览页，便于跳转到各工具。"""
    context = {
        "request": request,
        "manual_filter_url": request.url_for("manual_filter_page"),
        "dashboard_url": request.url_for("dashboard"),
        "search_url": request.url_for("articles_search_page"),
    }
    return templates.TemplateResponse("landing.html", context)


__all__ = ["router"]
