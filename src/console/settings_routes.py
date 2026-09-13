from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.adapters.db_postgres_app_config import (
    ConfigTargetNotEmptyError,
    ConfigVersionConflictError,
    CrawlAccountConflictError,
)
from src.console import settings_service
from src.console.auth_service import ConsoleUser
from src.console.security import require_role
from src.console.settings_schemas import (
    CrawlAccountBulkRequest,
    CrawlAccountCreateRequest,
    CrawlAccountPreviewRequest,
    CrawlAccountRefreshNamesRequest,
    CrawlAccountUpdateRequest,
    ModelTestRequest,
    SettingUpdateRequest,
)

router = APIRouter(prefix="/api/admin", tags=["settings"])


def _raise_service_error(exc: Exception) -> NoReturn:
    if isinstance(exc, ConfigVersionConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, (CrawlAccountConflictError, ConfigTargetNotEmptyError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="配置对象不存在") from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    return settings_service.get_settings_payload()


@router.put("/settings/{section}")
def put_setting(
    section: str,
    payload: SettingUpdateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        item = settings_service.update_setting(
            section,
            value=payload.value,
            expected_version=payload.expected_version,
            actor=user,
        )
    except Exception as exc:
        _raise_service_error(exc)
    return {"item": item}


@router.post("/settings/llm_models/test")
def test_model(payload: ModelTestRequest) -> dict[str, Any]:
    try:
        return settings_service.test_llm_model(
            payload.step,
            payload.model,
            payload.reasoning,
        )
    except ValueError as exc:
        _raise_service_error(exc)


@router.get("/crawl-accounts")
def get_crawl_accounts(source: str = Query(...)) -> dict[str, Any]:
    try:
        return {"items": settings_service.list_accounts(source)}
    except ValueError as exc:
        _raise_service_error(exc)


@router.post("/crawl-accounts", status_code=status.HTTP_201_CREATED)
def post_crawl_account(
    payload: CrawlAccountCreateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        item = settings_service.create_account(
            source=payload.source,
            text=payload.text,
            actor=user,
        )
    except Exception as exc:
        _raise_service_error(exc)
    return {"item": item}


@router.post("/crawl-accounts/preview")
def preview_crawl_accounts(
    payload: CrawlAccountPreviewRequest,
) -> dict[str, Any]:
    try:
        return {"items": settings_service.preview_accounts(payload.source, payload.text)}
    except ValueError as exc:
        _raise_service_error(exc)


@router.post("/crawl-accounts/bulk")
def bulk_crawl_accounts(
    payload: CrawlAccountBulkRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        items = settings_service.bulk_create_accounts(
            source=payload.source,
            text=payload.text,
            actor=user,
        )
    except Exception as exc:
        _raise_service_error(exc)
    return {"items": items}


@router.patch("/crawl-accounts/{account_id}")
def patch_crawl_account(
    account_id: str,
    payload: CrawlAccountUpdateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        item = settings_service.update_account(
            account_id,
            enabled=payload.enabled,
            set_enabled="enabled" in payload.model_fields_set,
            actor=user,
        )
    except Exception as exc:
        _raise_service_error(exc)
    return {"item": item}


@router.post("/crawl-accounts/refresh-names")
def refresh_crawl_account_names(
    payload: CrawlAccountRefreshNamesRequest,
) -> dict[str, Any]:
    return {"items": settings_service.refresh_account_names(payload.account_ids)}


@router.delete("/crawl-accounts/{account_id}")
def remove_crawl_account(
    account_id: str,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    try:
        item = settings_service.delete_account(account_id, actor=user)
    except Exception as exc:
        _raise_service_error(exc)
    return {"item": item}


__all__ = ["router"]
