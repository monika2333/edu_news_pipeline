from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src.adapters.db_postgres_core import get_adapter
from src.adapters.http_beijinghao import parse_column_input
from src.adapters.http_btime import parse_uid_input
from src.adapters.http_tencent import parse_author_input as parse_tencent_author
from src.adapters.http_toutiao import parse_author_input as parse_toutiao_author
from src.adapters.llm_chat import (
    apply_reasoning_config,
    build_headers,
    post_chat_completion,
)
from src.business_config import (
    ACCOUNT_SOURCES,
    LLM_STEPS,
    LLM_STEP_LABELS,
    SOURCE_BY_KEY,
    SOURCE_CATALOG,
    normalize_source_key,
    normalize_source_list,
    resolve_llm_steps,
    validate_crawl_sources,
    validate_section,
)
from src.config import BGE_EMBEDDING_MODEL, get_settings, load_environment
from src.console.auth_service import ConsoleUser


class SettingsPermissionError(PermissionError):
    """Raised when a persistent administrator identity is required."""


def _require_actor(actor: ConsoleUser) -> str:
    if actor.role != "admin" or not actor.user_id:
        raise SettingsPermissionError("配置修改必须使用已登录的管理员账号")
    return actor.user_id


def _modifier(row: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "user_id": str(row.get(f"{prefix}_by_user_id") or "") or None,
        "display_name": row.get(f"{prefix}_by_display_name"),
    }


def get_settings_payload() -> dict[str, Any]:
    adapter = get_adapter()
    rows = adapter.app_config.fetch_settings()
    sections = {
        str(row["section"]): {
            "value": row["value"],
            "version": int(row["version"]),
            "updated_at": row["updated_at"],
            "updated_by": _modifier(row, "updated"),
        }
        for row in rows
    }
    settings = get_settings()
    return {
        "sections": sections,
        "sources": [
            {
                "key": item.key,
                "display_name": item.display_name,
                "daily_only": item.daily_only,
                "requires_accounts": item.requires_accounts,
            }
            for item in SOURCE_CATALOG
        ],
        "steps": [
            {"key": step, "display_name": LLM_STEP_LABELS[step]}
            for step in LLM_STEPS
        ],
        "environment": {
            "llm_api_key_configured": bool(settings.llm_api_key),
            "llm_api_base_url": settings.llm_api_base_url,
            "embedding_model": BGE_EMBEDDING_MODEL,
        },
    }


def test_llm_model(step: str, model: str, reasoning: bool) -> dict[str, Any]:
    if step not in LLM_STEPS:
        raise ValueError(f"未知模型步骤：{step}")
    normalized_model = model.strip()
    if not normalized_model:
        raise ValueError("模型名不能为空")
    settings = get_settings()
    if not settings.llm_api_key:
        return {"success": False, "elapsed_ms": 0, "error": "LLM_API_KEY 未配置"}
    payload: dict[str, Any] = {
        "model": normalized_model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "temperature": 0,
        "max_tokens": 8,
    }
    apply_reasoning_config(
        payload,
        settings=settings,
        enabled=reasoning,
    )
    started = time.monotonic()
    try:
        post_chat_completion(
            f"{settings.llm_api_base_url.rstrip('/')}/chat/completions",
            payload=payload,
            headers=build_headers(
                api_key=settings.llm_api_key,
                referer=settings.llm_api_http_referer,
                title=settings.llm_api_title,
            ),
            timeout=min(10, settings.llm_scoring_timeout),
            budget=12,
            retries=1,
            retryable_statuses=set(),
            operation=f"settings_model_test:{step}",
            model=normalized_model,
            retry_non_retryable_statuses=False,
        )
    except Exception as exc:
        return {
            "success": False,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": str(exc),
        }
    return {
        "success": True,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "error": None,
    }


def update_setting(
    section: str,
    *,
    value: Any,
    expected_version: int,
    actor: ConsoleUser,
) -> dict[str, Any]:
    actor_user_id = _require_actor(actor)
    normalized = validate_section(section, value)
    adapter = get_adapter()
    current = adapter.app_config.fetch_setting(section)
    if current is None:
        raise KeyError(section)
    if section == "llm_models":
        before_steps = resolve_llm_steps(current["value"])
        after_steps = resolve_llm_steps(normalized)
        tested_combinations: set[tuple[str, bool]] = set()
        for step in LLM_STEPS:
            if before_steps[step] == after_steps[step]:
                continue
            config = after_steps[step]
            combination = (config.model, config.reasoning)
            if combination in tested_combinations:
                continue
            tested_combinations.add(combination)
            outcome = test_llm_model(step, config.model, config.reasoning)
            if not outcome["success"]:
                raise ValueError(
                    f"{LLM_STEP_LABELS[step]}模型测试失败：{outcome['error']}"
                )
    return adapter.update_app_setting_as_user(
        section=section,
        value=normalized,
        expected_version=expected_version,
        actor_user_id=actor_user_id,
    )


def _account_parser(source: str) -> Callable[[str], dict[str, str]]:
    normalized_source = normalize_source_key(source)
    if normalized_source == "toutiao":
        def parse(raw: str) -> dict[str, str]:
            identifier, profile_url = parse_toutiao_author(raw)
            return {"normalized_identifier": identifier, "profile_url": profile_url}

        return parse
    if normalized_source == "tencent":
        def parse(raw: str) -> dict[str, str]:
            item = parse_tencent_author(raw)
            return {
                "normalized_identifier": item.author_id,
                "profile_url": item.profile_url,
            }

        return parse
    if normalized_source == "btime":
        def parse(raw: str) -> dict[str, str]:
            item = parse_uid_input(raw)
            return {
                "normalized_identifier": item.uid,
                "profile_url": item.profile_url,
            }

        return parse
    if normalized_source == "beijinghao":
        def parse(raw: str) -> dict[str, str]:
            item = parse_column_input(raw)
            return {
                "normalized_identifier": item.column_code,
                "profile_url": item.page_url,
            }

        return parse
    raise ValueError(f"{source} 不支持账号配置")


def parse_account(source: str, raw: str) -> dict[str, str]:
    normalized_source = normalize_source_key(source)
    if normalized_source not in ACCOUNT_SOURCES:
        raise ValueError(f"{source} 不支持账号配置")
    cleaned = (raw or "").strip().lstrip("\ufeff")
    parsed = _account_parser(normalized_source)(cleaned)
    return {
        "source": normalized_source,
        "normalized_identifier": parsed["normalized_identifier"],
        "original_input": cleaned,
        "profile_url": parsed["profile_url"],
    }


def list_accounts(source: str) -> list[dict[str, Any]]:
    normalized_source = normalize_source_key(source)
    if normalized_source not in ACCOUNT_SOURCES:
        raise ValueError(f"{source} 不支持账号配置")
    return get_adapter().app_config.fetch_accounts(normalized_source)


def preview_accounts(source: str, text: str) -> list[dict[str, Any]]:
    normalized_source = normalize_source_key(source)
    if normalized_source not in ACCOUNT_SOURCES:
        raise ValueError(f"{source} 不支持账号配置")
    existing = {
        str(row["normalized_identifier"])
        for row in get_adapter().app_config.fetch_accounts(normalized_source)
    }
    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        cleaned = raw_line.strip().lstrip("\ufeff")
        if not cleaned or cleaned.startswith("#"):
            continue
        base = {"line_number": line_number, "input": cleaned}
        try:
            parsed = parse_account(normalized_source, cleaned)
        except ValueError as exc:
            results.append({**base, "status": "invalid", "error": str(exc)})
            continue
        identifier = parsed["normalized_identifier"]
        if identifier in existing:
            status = "existing_duplicate"
        elif identifier in seen:
            status = "batch_duplicate"
        else:
            status = "addable"
            seen.add(identifier)
        results.append({**base, **parsed, "status": status, "error": None})
    return results


def create_account(
    *,
    source: str,
    text: str,
    display_name: Optional[str],
    actor: ConsoleUser,
) -> dict[str, Any]:
    actor_user_id = _require_actor(actor)
    parsed = parse_account(source, text)
    return get_adapter().create_crawl_account_as_user(
        **parsed,
        display_name=(display_name or "").strip() or None,
        actor_user_id=actor_user_id,
    )


def bulk_create_accounts(
    *,
    source: str,
    text: str,
    actor: ConsoleUser,
) -> list[dict[str, Any]]:
    actor_user_id = _require_actor(actor)
    preview = preview_accounts(source, text)
    addable = [
        {
            "source": item["source"],
            "normalized_identifier": item["normalized_identifier"],
            "original_input": item["original_input"],
            "profile_url": item["profile_url"],
            "display_name": None,
        }
        for item in preview
        if item["status"] == "addable"
    ]
    created = get_adapter().create_crawl_accounts_as_user(
        accounts=addable,
        actor_user_id=actor_user_id,
    )
    created_by_identifier = {
        str(item["normalized_identifier"]): item for item in created
    }
    return [
        {
            **item,
            "account": created_by_identifier.get(
                str(item.get("normalized_identifier") or "")
            ),
        }
        for item in preview
    ]


def update_account(
    account_id: str,
    *,
    display_name: Optional[str],
    set_display_name: bool,
    enabled: Optional[bool],
    set_enabled: bool,
    actor: ConsoleUser,
) -> dict[str, Any]:
    if not set_display_name and not set_enabled:
        raise ValueError("至少提交一个可修改字段")
    return get_adapter().update_crawl_account_as_user(
        account_id=account_id,
        display_name=(display_name or "").strip() or None,
        set_display_name=set_display_name,
        enabled=enabled,
        set_enabled=set_enabled,
        actor_user_id=_require_actor(actor),
    )


def delete_account(account_id: str, *, actor: ConsoleUser) -> dict[str, Any]:
    return get_adapter().delete_crawl_account_as_user(
        account_id=account_id,
        actor_user_id=_require_actor(actor),
    )


def _legacy_models() -> dict[str, Any]:
    default = os.getenv("LLM_MODEL") or "deepseek/deepseek-v4-flash"
    summary = os.getenv("LLM_SUMMARY_MODEL") or default
    scoring = os.getenv("LLM_SCORING_MODEL") or default
    resolved = {
        "summary": summary,
        "source": os.getenv("LLM_SOURCE_MODEL") or summary,
        "sentiment": os.getenv("LLM_SENTIMENT_MODEL") or summary,
        "scoring": scoring,
        "external_filter": os.getenv("LLM_EXTERNAL_FILTER_MODEL") or scoring,
        "beijing_gate": os.getenv("LLM_BEIJING_GATE_MODEL") or scoring,
        "duplicate_review": scoring,
    }
    global_reasoning = _legacy_bool("LLM_REASONING_ENABLED", default=True)
    reasoning = {
        "summary": _legacy_bool(
            "LLM_SUMMARY_REASONING_ENABLED",
            default=False,
        ),
        "source": _legacy_bool(
            "LLM_SOURCE_REASONING_ENABLED",
            default=True,
        ),
        "sentiment": _legacy_bool(
            "LLM_SENTIMENT_REASONING_ENABLED",
            default=True,
        ),
        "scoring": global_reasoning,
        "external_filter": global_reasoning,
        "beijing_gate": global_reasoning,
        "duplicate_review": global_reasoning,
    }
    return {
        "default": default,
        "steps": {
            step: {
                "model": None if model == default else model,
                "reasoning": reasoning[step],
            }
            for step, model in resolved.items()
        },
    }


def _legacy_bool(key: str, *, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _legacy_account_paths(root: Path) -> dict[str, Path]:
    tencent_default = root / "config/qq_author.txt"
    if not tencent_default.exists():
        tencent_default = root / "newsqq_crawl/qq_author.txt"
    candidates = {
        "toutiao": (
            os.getenv("TOUTIAO_AUTHORS_PATH"),
            root / "config/toutiao_author.txt",
        ),
        "tencent": (os.getenv("TENCENT_AUTHORS_PATH"), tencent_default),
        "btime": (os.getenv("BTIME_UIDS_PATH"), root / "config/btime_author.txt"),
        "beijinghao": (
            os.getenv("BEIJINGHAO_COLUMNS_PATH"),
            root / "config/beijinghao_author.txt",
        ),
    }
    paths: dict[str, Path] = {}
    for source, (configured, default) in candidates.items():
        if configured:
            candidate = Path(configured).expanduser()
            paths[source] = candidate if candidate.is_absolute() else Path.cwd() / candidate
        else:
            paths[source] = default
    return paths


def preview_legacy_import(*, root: Optional[Path] = None) -> dict[str, Any]:
    load_environment()
    repository_root = root or Path(__file__).resolve().parents[2]
    raw_sources = os.getenv("CRAWL_SOURCES") or "toutiao"
    source_tokens = [item.strip() for item in raw_sources.split(",") if item.strip()]
    normalized_sources = normalize_source_list(source_tokens, allow_daily=True)
    daily_sources = [
        key for key in normalized_sources if SOURCE_BY_KEY[key].daily_only
    ]
    accounts: list[dict[str, Any]] = []
    account_summary: dict[str, Any] = {}
    has_parse_errors = False
    for source, path in _legacy_account_paths(repository_root).items():
        seen: set[str] = set()
        duplicates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        parsed_count = 0
        if path.exists():
            for line_number, raw_line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                cleaned = raw_line.strip().lstrip("\ufeff")
                if not cleaned or cleaned.startswith("#"):
                    continue
                try:
                    parsed = parse_account(source, cleaned)
                except ValueError as exc:
                    errors.append(
                        {
                            "line_number": line_number,
                            "input": cleaned,
                            "error": str(exc),
                        }
                    )
                    has_parse_errors = True
                    continue
                identifier = parsed["normalized_identifier"]
                if identifier in seen:
                    duplicates.append(
                        {
                            "line_number": line_number,
                            "input": cleaned,
                            "normalized_identifier": identifier,
                        }
                    )
                    continue
                seen.add(identifier)
                parsed_count += 1
                accounts.append({**parsed, "display_name": None, "enabled": True})
        account_summary[source] = {
            "path": str(path),
            "parsed_count": parsed_count,
            "duplicates": duplicates,
            "errors": errors,
        }
    sections = {
        "llm_models": validate_section("llm_models", _legacy_models()),
        "crawl_sources": validate_crawl_sources(
            normalized_sources,
            allow_daily=True,
        ),
    }
    return {
        "sections": sections,
        "account_summary": account_summary,
        "accounts": accounts,
        "daily_only_sources": daily_sources,
        "has_parse_errors": has_parse_errors,
    }


def import_legacy_config(*, apply: bool, root: Optional[Path] = None) -> dict[str, Any]:
    preview = preview_legacy_import(root=root)
    if not apply:
        return preview
    if preview["daily_only_sources"]:
        joined = ", ".join(preview["daily_only_sources"])
        raise ValueError(f"每小时来源含仅每日任务来源，拒绝导入：{joined}")
    if preview["has_parse_errors"]:
        raise ValueError("账号文件存在无法解析的行，拒绝导入")
    get_adapter().import_app_config(
        sections=preview["sections"],
        accounts=preview["accounts"],
    )
    return preview


__all__ = [
    "SettingsPermissionError",
    "bulk_create_accounts",
    "create_account",
    "delete_account",
    "get_settings_payload",
    "import_legacy_config",
    "list_accounts",
    "parse_account",
    "preview_accounts",
    "preview_legacy_import",
    "test_llm_model",
    "update_account",
    "update_setting",
]
