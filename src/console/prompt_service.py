from __future__ import annotations

import logging
from typing import Any, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.adapters.prompt_store import load_active_prompt, load_active_prompts
from src.console.auth_service import ConsoleUser
from src.domain.prompt import (
    MAX_PROMPT_CONTENT_LENGTH,
    MAX_PROMPT_NOTE_LENGTH,
    PROMPT_DEFINITIONS,
    PromptDefinition,
    PromptTemplate,
    format_prompt_version,
    prompt_definition,
)

logger = logging.getLogger(__name__)

PROMPT_HISTORY_DEFAULT_LIMIT = 20
PROMPT_HISTORY_MAX_LIMIT = 100


def _require_actor(actor: ConsoleUser) -> str:
    if actor.role != "admin" or not actor.user_id:
        raise PermissionError("提示词修改必须使用已登录的管理员账号")
    return actor.user_id


def _prompt_from_row(
    definition: PromptDefinition,
    row: dict[str, Any],
) -> PromptTemplate:
    revision = int(row["revision"])
    return PromptTemplate(
        key=definition.key,
        version=format_prompt_version(revision),
        content=str(row["content"]).strip(),
        source="database",
        revision=revision,
        revision_id=int(row["id"]),
        updated_at=row.get("created_at"),
        updated_by_user_id=(
            str(row["created_by_user_id"])
            if row.get("created_by_user_id")
            else None
        ),
        updated_by_display_name=row.get("created_by_display_name"),
    )


def _serialize_item(
    definition: PromptDefinition,
    prompt: PromptTemplate,
) -> dict[str, Any]:
    return {
        "key": definition.key,
        "label": definition.label,
        "description": definition.description,
        "step": definition.step,
        "kind": definition.kind,
        "content": prompt.content,
        "version": prompt.version,
        "revision": prompt.revision,
        "revision_id": prompt.revision_id,
        "source": prompt.source,
        "char_count": len(prompt.content),
        "updated_at": prompt.updated_at,
        "updated_by": (
            {
                "user_id": prompt.updated_by_user_id,
                "display_name": prompt.updated_by_display_name,
            }
            if prompt.updated_by_user_id or prompt.updated_by_display_name
            else None
        ),
    }


def get_prompts_payload(adapter: Optional[Any] = None) -> dict[str, Any]:
    resolved_adapter = adapter or get_adapter()
    prompts = load_active_prompts(resolved_adapter)
    return {
        "items": [
            _serialize_item(definition, prompts[definition.key])
            for definition in PROMPT_DEFINITIONS
        ]
    }


def _normalize_content(content: str) -> str:
    text = str(content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise ValueError("提示词内容不能为空")
    if len(text) > MAX_PROMPT_CONTENT_LENGTH:
        raise ValueError(
            f"提示词内容不能超过 {MAX_PROMPT_CONTENT_LENGTH} 个字符（当前 {len(text)}）"
        )
    lowered = text.lower()
    if "<prompt>" in lowered or "</prompt>" in lowered:
        raise ValueError("只填写 <prompt> 标签内的正文，不要包含标签本身")
    return text


def _normalize_note(note: Optional[str]) -> Optional[str]:
    text = str(note or "").strip()
    if not text:
        return None
    if len(text) > MAX_PROMPT_NOTE_LENGTH:
        raise ValueError(f"修改说明不能超过 {MAX_PROMPT_NOTE_LENGTH} 个字符")
    return text


def save_prompt(
    prompt_key: str,
    *,
    content: str,
    expected_revision_id: Optional[int],
    note: Optional[str],
    actor: ConsoleUser,
) -> dict[str, Any]:
    definition = prompt_definition(prompt_key)
    actor_user_id = _require_actor(actor)
    normalized_content = _normalize_content(content)
    normalized_note = _normalize_note(note)
    adapter = get_adapter()
    current = load_active_prompt(definition.key, adapter)
    if normalized_content == current.content:
        raise ValueError("提示词内容未发生变化")
    if expected_revision_id != current.revision_id:
        raise PromptRevisionConflictError(
            f"提示词已在别处被修改（当前版本：{current.version}）"
        )

    baseline = None
    if current.source == "file" and current.content.strip():
        baseline = {
            "revision": current.revision or 1,
            "content": current.content,
        }
    row = adapter.prompt_revisions.append_revision_as_user(
        prompt_key=definition.key,
        content=normalized_content,
        note=normalized_note,
        expected_revision_id=expected_revision_id,
        baseline=baseline,
        actor_user_id=actor_user_id,
    )
    logger.info(
        "用户 %s 保存了提示词 %s（v%s）",
        actor.display_name or actor.username,
        definition.key,
        row["revision"],
    )
    return _serialize_item(definition, _prompt_from_row(definition, row))


def list_prompt_revisions(
    prompt_key: str,
    *,
    limit: int = PROMPT_HISTORY_DEFAULT_LIMIT,
    adapter: Optional[Any] = None,
) -> dict[str, Any]:
    definition = prompt_definition(prompt_key)
    resolved_limit = max(1, min(int(limit or PROMPT_HISTORY_DEFAULT_LIMIT), PROMPT_HISTORY_MAX_LIMIT))
    resolved_adapter = adapter or get_adapter()
    rows = resolved_adapter.prompt_revisions.fetch_revisions(
        definition.key,
        limit=resolved_limit + 1,
    )
    has_more = len(rows) > resolved_limit
    items = []
    for row in rows[:resolved_limit]:
        revision = int(row["revision"])
        created_by_user_id = (
            str(row["created_by_user_id"])
            if row.get("created_by_user_id")
            else None
        )
        items.append(
            {
                "id": int(row["id"]),
                "revision": revision,
                "version": format_prompt_version(revision),
                "note": row.get("note"),
                "char_count": len(str(row["content"])),
                "created_at": row.get("created_at"),
                "created_by": (
                    {
                        "user_id": created_by_user_id,
                        "display_name": row.get("created_by_display_name"),
                    }
                    if created_by_user_id or row.get("created_by_display_name")
                    else None
                ),
            }
        )
    return {"items": items, "has_more": has_more}


def get_prompt_revision(
    prompt_key: str,
    revision_id: int,
    *,
    adapter: Optional[Any] = None,
) -> dict[str, Any]:
    definition = prompt_definition(prompt_key)
    resolved_adapter = adapter or get_adapter()
    row = resolved_adapter.prompt_revisions.fetch_revision(
        definition.key,
        int(revision_id),
    )
    if row is None:
        raise KeyError(f"提示词版本不存在：{revision_id}")
    revision = int(row["revision"])
    created_by_user_id = (
        str(row["created_by_user_id"]) if row.get("created_by_user_id") else None
    )
    return {
        "id": int(row["id"]),
        "prompt_key": definition.key,
        "revision": revision,
        "version": format_prompt_version(revision),
        "content": str(row["content"]),
        "note": row.get("note"),
        "char_count": len(str(row["content"])),
        "created_at": row.get("created_at"),
        "created_by": (
            {
                "user_id": created_by_user_id,
                "display_name": row.get("created_by_display_name"),
            }
            if created_by_user_id or row.get("created_by_display_name")
            else None
        ),
    }


__all__ = [
    "PROMPT_HISTORY_DEFAULT_LIMIT",
    "PROMPT_HISTORY_MAX_LIMIT",
    "get_prompt_revision",
    "get_prompts_payload",
    "list_prompt_revisions",
    "save_prompt",
]
