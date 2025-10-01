from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from src.console.schemas.export import LatestExport
from src.console.services import exports as exports_service

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/latest", response_model=LatestExport, summary="Fetch latest export batch")
def latest_export(include_items: bool = Query(False, description="Include exported item details")) -> LatestExport:
    result = exports_service.get_latest_export(include_items=include_items)
    if result is None:
        raise HTTPException(status_code=404, detail="No export batches found")
    return LatestExport.model_validate(result)


__all__ = ["router"]
