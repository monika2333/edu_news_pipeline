from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from src.business_config import warn_legacy_config
from src.config import refresh_environment_if_changed
from src.console import (
    admin_summary_routes,
    articles_routes,
    auth_routes,
    duty_review_routes,
    health_routes,
    manual_filter_routes,
    prompts_routes,
    runs_routes,
    settings_routes,
    shifts_routes,
    submission_archive_routes,
    users_routes,
    web_routes,
)
from src.console.security import (
    require_admin_workspace_user,
    require_console_user,
    require_csrf,
    require_role,
)


FAVICON_PATH = Path(__file__).parent / "web_static" / "favicon.svg"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application for the console service."""
    warn_legacy_config()
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

    # env 文件改过就自动重读（改 .env.local 免重启生效的统一入口）：
    # 检查只是 stat 文件信息，没变化时零开销。放在中间件一处统一做，
    # 不散落在各个接口里——查重复核这类在控制台进程里调用模型的功能
    # 也因此能马上用上新 Key。流水线各步骤是计划任务新起的进程，启动时
    # 本来就读最新文件，不经过这里（一轮运行中途换 Key 会让前后行为不一致）。
    @app.middleware("http")
    async def refresh_env_files_middleware(request: Request, call_next):
        refresh_environment_if_changed()
        return await call_next(request)

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
    admin_workspace_dependencies = [
        Depends(require_admin_workspace_user),
        Depends(require_csrf),
    ]

    app.include_router(health_routes.router)
    app.include_router(auth_routes.router)
    app.include_router(runs_routes.router, dependencies=admin_dependencies)
    app.include_router(settings_routes.router, dependencies=admin_dependencies)
    app.include_router(prompts_routes.router, dependencies=admin_dependencies)
    app.include_router(articles_routes.router, dependencies=protected_dependencies)
    app.include_router(
        manual_filter_routes.router,
        dependencies=admin_workspace_dependencies,
    )
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
