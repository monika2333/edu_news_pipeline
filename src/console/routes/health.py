from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Service health probe")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", summary="Root redirect")
def root() -> dict[str, str]:
    return {"message": "Edu News console is running"}


__all__ = ["router"]
