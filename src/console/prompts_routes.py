from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query

from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.console import prompt_service, prompt_test_service
from src.console.auth_service import ConsoleUser
from src.console.prompt_schemas import PromptTestRequest, PromptUpdateRequest
from src.console.security import require_role

router = APIRouter(prefix="/api/admin/prompts", tags=["prompts"])


def _raise_prompt_error(exc: Exception) -> NoReturn:
    if isinstance(exc, PromptRevisionConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="提示词不存在") from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _raise_prompt_test_error(exc: Exception) -> NoReturn:
    if isinstance(exc, prompt_test_service.PromptTestArticleNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="提示词不存在") from exc
    if isinstance(exc, prompt_test_service.PromptTestArticleUnavailableError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


@router.get("")
def get_prompts() -> dict[str, Any]:
    """Return the five prompts with their current revision and content."""
    return prompt_service.get_prompts_payload()


@router.put("/{prompt_key}")
def put_prompt(
    prompt_key: str,
    payload: PromptUpdateRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Append a new prompt revision; the version label is assigned server-side."""
    try:
        item = prompt_service.save_prompt(
            prompt_key,
            content=payload.content,
            expected_revision_id=payload.expected_revision_id,
            note=payload.note,
            actor=user,
        )
    except Exception as exc:
        _raise_prompt_error(exc)
    return {"item": item}


@router.post("/{prompt_key}/test")
def test_prompt(
    prompt_key: str,
    payload: PromptTestRequest,
    user: ConsoleUser = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Run one article through the current or draft prompt; never writes state."""
    try:
        return prompt_test_service.test_prompt(
            prompt_key,
            article_id=payload.article_id,
            content=payload.content,
            actor=user,
        )
    except Exception as exc:
        _raise_prompt_test_error(exc)


@router.get("/{prompt_key}/versions")
def get_prompt_versions(
    prompt_key: str,
    limit: int = Query(
        prompt_service.PROMPT_HISTORY_DEFAULT_LIMIT,
        ge=1,
        le=prompt_service.PROMPT_HISTORY_MAX_LIMIT,
    ),
) -> dict[str, Any]:
    """List revision metadata; content is fetched per revision on demand."""
    try:
        return prompt_service.list_prompt_revisions(prompt_key, limit=limit)
    except Exception as exc:
        _raise_prompt_error(exc)


@router.get("/{prompt_key}/versions/{revision_id}")
def get_prompt_version(
    prompt_key: str,
    revision_id: int,
) -> dict[str, Any]:
    """Return one revision including its content, used by view/restore."""
    try:
        return prompt_service.get_prompt_revision(prompt_key, revision_id)
    except Exception as exc:
        _raise_prompt_error(exc)


__all__ = ["router"]
