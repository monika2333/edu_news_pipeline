from __future__ import annotations

from fastapi import Depends, FastAPI

from src.console.routes import articles, exports, health, runs, web
from src.console.security import require_console_user


def create_app() -> FastAPI:
    """Build and configure the FastAPI application for the console service."""
    app = FastAPI(
        title="Edu News Console",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    protected_dependencies = [Depends(require_console_user)]

    app.include_router(health.router)
    app.include_router(runs.router, dependencies=protected_dependencies)
    app.include_router(articles.router, dependencies=protected_dependencies)
    app.include_router(exports.router, dependencies=protected_dependencies)
    app.include_router(web.router, dependencies=protected_dependencies)
    return app


app = create_app()


__all__ = ["create_app", "app"]
