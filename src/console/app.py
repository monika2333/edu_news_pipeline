from __future__ import annotations

from fastapi import FastAPI

from src.console.routes import health, runs


def create_app() -> FastAPI:
    """Build and configure the FastAPI application for the console service."""
    app = FastAPI(
        title="Edu News Console",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.include_router(health.router)
    app.include_router(runs.router)
    return app


app = create_app()


__all__ = ["create_app", "app"]
