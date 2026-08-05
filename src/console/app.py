from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from src.console import (
    admin_summary_routes,
    articles_routes,
    auth_routes,
    duty_review_routes,
    exports_routes,
    health_routes,
    manual_filter_routes,
    runs_routes,
    shifts_routes,
    submission_archive_routes,
    users_routes,
    web_routes,
)
from src.console.security import require_console_user, require_csrf, require_role


FAVICON_PATH = Path(__file__).parent / "web_static" / "favicon.svg"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application for the console service."""
    app = FastAPI(
        title="Edu News Console",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    @app.exception_handler(HTTPException)
    async def console_http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> Response:
        if (
            exc.status_code == status.HTTP_401_UNAUTHORIZED
            and not request.url.path.startswith("/api/")
            and request.url.path != "/login"
        ):
            next_path = request.url.path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            return RedirectResponse(
                url=f"/login?next={quote(next_path, safe='/')}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        return await http_exception_handler(request, exc)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        """Return the browser tab icon without requiring authentication."""
        return FileResponse(FAVICON_PATH, media_type="image/svg+xml")

    # Mount static files
    app.mount("/static", StaticFiles(directory="src/console/web_static"), name="static")

    admin_dependencies = [
        Depends(require_role("admin")),
        Depends(require_csrf),
    ]
    protected_dependencies = [
        Depends(require_console_user),
        Depends(require_csrf),
    ]

    app.include_router(health_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(runs_routes.router, dependencies=admin_dependencies)
    app.include_router(articles_routes.router, dependencies=protected_dependencies)
    app.include_router(exports_routes.router, dependencies=admin_dependencies)
    app.include_router(manual_filter_routes.router, dependencies=admin_dependencies)
    app.include_router(
        admin_summary_routes.router,
        dependencies=protected_dependencies,
    )
    app.include_router(shifts_routes.router, dependencies=protected_dependencies)
    app.include_router(users_routes.router, dependencies=protected_dependencies)
    app.include_router(
        duty_review_routes.router,
        dependencies=protected_dependencies,
    )
    app.include_router(
        submission_archive_routes.router,
        dependencies=protected_dependencies,
    )
    app.include_router(web_routes.router)
    return app


app = create_app()


__all__ = ["create_app", "app"]
