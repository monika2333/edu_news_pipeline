from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.console.services import exports as exports_service
from src.console.services import runs as runs_service

router = APIRouter(tags=["console"], include_in_schema=False)

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


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


__all__ = ["router"]
