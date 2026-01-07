from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from src.console import articles_routes, exports_routes, health_routes, manual_filter_routes, runs_routes, web_routes
from src.console.security import require_console_user


def create_app() -> FastAPI:
    """Build and configure the FastAPI application for the console service."""
    app = FastAPI(
        title="Edu News Console",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    
    # Mount static files
    app.mount("/static", StaticFiles(directory="src/console/web_static"), name="static")

    protected_dependencies = [Depends(require_console_user)]

    app.include_router(health_routes.router)
    app.include_router(runs_routes.router, dependencies=protected_dependencies)
    app.include_router(articles_routes.router, dependencies=protected_dependencies)
    app.include_router(exports_routes.router, dependencies=protected_dependencies)
    app.include_router(manual_filter_routes.router, dependencies=protected_dependencies)
    app.include_router(web_routes.router, dependencies=protected_dependencies)
    return app


app = create_app()


__all__ = ["create_app", "app"]
