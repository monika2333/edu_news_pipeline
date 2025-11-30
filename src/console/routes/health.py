from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Service health probe")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


__all__ = ["router"]
