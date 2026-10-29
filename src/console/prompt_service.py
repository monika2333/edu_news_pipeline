from __future__ import annotations

import logging
from typing import Any, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.db_postgres_prompt_revisions import PromptRevisionConflictError
from src.adapters.prompt_store import load_active_prompt, load_active_prompts
from src.console.auth_service import ConsoleUser
from src.domain.prompt import (
    MAX_PROMPT_NOTE_LENGTH,
    PROMPT_DEFINITIONS,
    PromptDefinition,
    PromptTemplate,
    format_prompt_version,
    normalize_prompt_content,
    prompt_definition,
)

logger = logging.getLogger(__name__)

PROMPT_HISTORY_DEFAULT_LIMIT = 20
PROMPT_HISTORY_MAX_LIMIT = 100
OPTIMIZATION_OUTCOME_LABELS = {
    "no_correction": "无需修正",
    "model_declined": "模型判断不改",
    "check_failed": "未通过检查",
    "revision_created": "已生成新版本",
    "error": "出错",
}


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


def _last_check(run: dict[str, Any]) -> dict[str, Any]:
    attempts = run.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        return {}
    last = attempts[-1]
    if not isinstance(last, dict):
        return {}
    check = last.get("check")
    return check if isinstance(check, dict) else {}


def _optimization_status(run: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not run:
        return None
    check = _last_check(run)
    outcome = str(run.get("outcome") or "")
    return {
        "run_id": int(run["id"]),
        "outcome": outcome,
        "outcome_label": OPTIMIZATION_OUTCOME_LABELS.get(outcome, outcome),
        "finished_at": run.get("finished_at"),
        "window_start": run.get("window_start"),
        "new_revision_id": (
            int(run["new_revision_id"])
            if run.get("new_revision_id") is not None
            else None
        ),
        "new_version": (
            format_prompt_version(int(run["new_revision"]))
            if run.get("new_revision") is not None
            else None
        ),
        "fixed_count": int(check.get("fixed_count") or 0),
        "broken_count": int(check.get("broken_count") or 0),
        "t_total": int(check.get("t_total") or 0),
        "r_total": int(check.get("r_total") or 0),
        "error": run.get("error"),
    }


def _serialize_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": int(run["id"]),
        "outcome": run.get("outcome"),
        "outcome_label": OPTIMIZATION_OUTCOME_LABELS.get(
            str(run.get("outcome") or ""),
            run.get("outcome"),
        ),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "window_start": run.get("window_start"),
        "baseline_revision_id": run.get("baseline_revision_id"),
        "new_revision_id": run.get("new_revision_id"),
        "model": run.get("model"),
        "cases": run.get("cases") or [],
        "attempts": run.get("attempts") or [],
        "error": run.get("error"),
    }


def _revision_origin(row: dict[str, Any], run: Optional[dict[str, Any]]) -> str:
    if run:
        return "auto_optimized"
    if row.get("created_by_user_id"):
        return "user"
    if str(row.get("note") or "").strip() == "初始文件版本":
        return "file_baseline"
    return "system"


def get_prompts_payload(adapter: Optional[Any] = None) -> dict[str, Any]:
    resolved_adapter = adapter or get_adapter()
    prompts = load_active_prompts(resolved_adapter)
    optimization = getattr(resolved_adapter, "prompt_optimization", None)
    latest_runs = (
        optimization.fetch_latest_runs(
            [definition.key for definition in PROMPT_DEFINITIONS]
        )
        if optimization is not None
        else {}
    )
    return {
        "items": [
            {
                **_serialize_item(definition, prompts[definition.key]),
                "optimization_status": _optimization_status(
                    latest_runs.get(definition.key)
                ),
            }
            for definition in PROMPT_DEFINITIONS
        ]
    }


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
    normalized_content = normalize_prompt_content(content)
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
    optimization = getattr(resolved_adapter, "prompt_optimization", None)
    runs_by_revision = (
        optimization.fetch_runs_for_revision_ids(
            [int(row["id"]) for row in rows[:resolved_limit]]
        )
        if optimization is not None
        else {}
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
        run = runs_by_revision.get(int(row["id"]))
        items.append(
            {
                "id": int(row["id"]),
                "revision": revision,
                "version": format_prompt_version(revision),
                "note": row.get("note"),
                "char_count": len(str(row["content"])),
                "created_at": row.get("created_at"),
                "origin": _revision_origin(row, run),
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
    optimization = getattr(resolved_adapter, "prompt_optimization", None)
    run = (
        optimization.fetch_run_by_new_revision_id(int(row["id"]))
        if optimization is not None
        else None
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
        "origin": _revision_origin(row, run),
        "created_by": (
            {
                "user_id": created_by_user_id,
                "display_name": row.get("created_by_display_name"),
            }
            if created_by_user_id or row.get("created_by_display_name")
            else None
        ),
        "optimization": _serialize_run(run) if run else None,
    }


__all__ = [
    "PROMPT_HISTORY_DEFAULT_LIMIT",
    "PROMPT_HISTORY_MAX_LIMIT",
    "get_prompt_revision",
    "get_prompts_payload",
    "list_prompt_revisions",
    "normalize_prompt_content",
    "save_prompt",
]
