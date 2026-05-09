from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["console"], include_in_schema=False)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "web_templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


@router.get("/manual_filter", response_class=HTMLResponse)
async def manual_filter_page(request: Request) -> HTMLResponse:
    # Use current timestamp for cache busting, or could be a build version
    version = datetime.now().strftime("%Y%m%d%H%M%S")
    return templates.TemplateResponse("manual_filter.html", {"request": request, "version": version})


@router.get("/", include_in_schema=False)
async def root_page(request: Request) -> RedirectResponse:
    """Redirect the console root to the active manual filter workflow."""
    return RedirectResponse(url=request.url_for("manual_filter_page"), status_code=307)


__all__ = ["router"]
