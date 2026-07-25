from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.console.auth_service import ConsoleUser
from src.console.security import require_console_user, require_role
from src.console.shifts_service import (
    ShiftScheduleIncompleteError,
    generate_shifts,
)

router = APIRouter(tags=["console"], include_in_schema=False)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "web_templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


@router.get("/manual_filter", response_class=HTMLResponse)
async def manual_filter_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    # Use current timestamp for cache busting, or could be a build version
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse(
        "manual_filter.html",
        {
            "request": request,
            "version": version,
            "current_user": user,
            "workspace_mode": "admin",
        },
    )


@router.get("/", include_in_schema=False)
async def root_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> RedirectResponse:
    """Redirect the console root to the active manual filter workflow."""
    if user.role == "duty_editor":
        return RedirectResponse(url=request.url_for("duty_page"), status_code=307)
    return RedirectResponse(url=request.url_for("manual_filter_page"), status_code=307)


@router.get("/duty", response_class=HTMLResponse)
async def duty_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("duty_editor")),
) -> HTMLResponse:
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse(
        "manual_filter.html",
        {
            "request": request,
            "version": version,
            "current_user": user,
            "workspace_mode": "duty",
        },
    )


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    try:
        generate_shifts(days=14, actor_user_id=user.user_id)
    except ShiftScheduleIncompleteError:
        pass
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse(
        "admin.html",
        {"request": request, "version": version, "current_user": user},
    )


@router.get("/admin/duty-summary", response_class=HTMLResponse)
async def duty_summary_page(
    request: Request,
    user: ConsoleUser = Depends(require_role("admin")),
) -> HTMLResponse:
    try:
        generate_shifts(days=14, actor_user_id=user.user_id)
    except ShiftScheduleIncompleteError:
        pass
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse(
        "duty_summary.html",
        {"request": request, "version": version, "current_user": user},
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "version": version},
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse(
        "register.html",
        {"request": request, "version": version},
    )


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    user: ConsoleUser = Depends(require_console_user),
) -> HTMLResponse:
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse(
        "account.html",
        {"request": request, "version": version, "current_user": user},
    )


__all__ = ["router"]
