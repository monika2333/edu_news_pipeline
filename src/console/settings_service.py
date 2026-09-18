from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from src.adapters.account_profiles import (
    AccountNameUnavailable,
    resolve_account_name,
)
from src.adapters.db_postgres_core import get_adapter
from src.adapters.http_beijinghao import parse_column_input
from src.adapters.http_btime import parse_uid_input
from src.adapters.http_tencent import parse_author_input as parse_tencent_author
from src.adapters.http_toutiao import parse_author_input as parse_toutiao_author
from src.adapters.llm_chat import post_chat_completion
from src.adapters.llm_endpoint import (
    LLMEndpointKeyMissingError,
    resolve_endpoint,
)
from src.business_config import (
    ACCOUNT_SOURCES,
    LLMEndpointConfig,
    LLM_STEPS,
    LLM_STEP_LABELS,
    SOURCE_BY_KEY,
    SOURCE_CATALOG,
    normalize_source_key,
    normalize_source_list,
    resolve_llm_steps,
    resolve_llm_endpoints,
    validate_crawl_sources,
    validate_llm_endpoints,
    validate_llm_models,
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


def _endpoints_payload(settings: Any, sections: Mapping[str, Any]) -> dict[str, Any]:
    row = sections.get("llm_endpoints")
    if row is None:
        return {
            "default": None,
            "allowed_hosts": list(settings.llm_allowed_hosts),
            "items": [],
        }
    normalized = validate_llm_endpoints(row["value"])
    return {
        "default": normalized["default"],
        "allowed_hosts": list(settings.llm_allowed_hosts),
        "items": [
            {
                "key": item["key"],
                "label": item["label"],
                "base_url": item["base_url"],
                "api_key_env": item["api_key_env"],
                "api_style": item["api_style"],
                "temperature_override": item["temperature_override"],
                "api_key_env_configured": bool(os.getenv(item["api_key_env"])),
            }
            for item in normalized["items"]
        ],
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
        "endpoints": _endpoints_payload(settings, sections),
        "environment": {
            "llm_api_key_configured": bool(settings.llm_api_key),
            "embedding_model": BGE_EMBEDDING_MODEL,
        },
    }


def _endpoint_definition(endpoint_key: Optional[str]) -> LLMEndpointConfig:
    row = get_adapter().app_config.fetch_setting("llm_endpoints")
    if row is None:
        raise ValueError("接入点配置未初始化：数据库缺少 llm_endpoints 分区")
    items, default_key = resolve_llm_endpoints(row["value"])
    resolved = endpoint_key or default_key
    definition = items.get(resolved)
    if definition is None:
        raise ValueError(f"接入点不存在：{resolved}")
    return definition


def test_llm_model(
    step: str,
    model: str,
    reasoning: bool,
    endpoint: Optional[str] = None,
) -> dict[str, Any]:
    if step not in LLM_STEPS:
        raise ValueError(f"未知模型步骤：{step}")
    normalized_model = model.strip()
    if not normalized_model:
        raise ValueError("模型名不能为空")
    settings = get_settings()
    try:
        resolved = resolve_endpoint(_endpoint_definition(endpoint))
    except LLMEndpointKeyMissingError as exc:
        return {"success": False, "elapsed_ms": 0, "error": str(exc)}
    reasoning_limit = settings.llm_reasoning_max_tokens or 0
    max_tokens = max(2048, reasoning_limit * 2) if reasoning else 8
    request_timeout = (
        max(120, settings.llm_scoring_timeout)
        if reasoning
        else min(10, settings.llm_scoring_timeout)
    )
    request_budget = max(180, request_timeout + 30) if reasoning else 12
    payload: dict[str, Any] = {
        "model": normalized_model,
        "messages": [{"role": "user", "content": "只回复 OK"}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    resolved.finalize_payload(
        payload,
        settings=settings,
        reasoning_enabled=reasoning,
    )
    started = time.monotonic()
    try:
        post_chat_completion(
            resolved.chat_url,
            payload=payload,
            headers=resolved.headers(),
            timeout=request_timeout,
            budget=request_budget,
            retries=1,
            retryable_statuses=set(),
            operation=f"settings_model_test:{step}",
            model=normalized_model,
            retry_non_retryable_statuses=False,
            endpoint_label=resolved.label,
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


def _require_llm_endpoints(adapter: Any) -> tuple[dict[str, LLMEndpointConfig], str]:
    row = adapter.app_config.fetch_setting("llm_endpoints")
    if row is None:
        raise ValueError("接入点配置未初始化：数据库缺少 llm_endpoints 分区")
    return resolve_llm_endpoints(row["value"])


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
        endpoint_items, endpoint_default = _require_llm_endpoints(adapter)
        after_steps = resolve_llm_steps(normalized)
        for step in LLM_STEPS:
            endpoint_ref = after_steps[step].endpoint
            if endpoint_ref is not None and endpoint_ref not in endpoint_items:
                raise ValueError(
                    f"{LLM_STEP_LABELS[step]}引用了不存在的接入点：{endpoint_ref}"
                )
        before_steps = resolve_llm_steps(current["value"])
        tested_combinations: set[tuple[str, str, bool]] = set()
        for step in LLM_STEPS:
            if before_steps[step] == after_steps[step]:
                continue
            config = after_steps[step]
            resolved_endpoint = config.endpoint or endpoint_default
            combination = (resolved_endpoint, config.model, config.reasoning)
            if combination in tested_combinations:
                continue
            tested_combinations.add(combination)
            outcome = test_llm_model(
                step,
                config.model,
                config.reasoning,
                endpoint=resolved_endpoint,
            )
            if not outcome["success"]:
                raise ValueError(
                    f"{LLM_STEP_LABELS[step]}模型测试失败：{outcome['error']}"
                )
    elif section == "llm_endpoints":
        before_endpoints = validate_llm_endpoints(current["value"])
        removed_keys = {
            item["key"] for item in before_endpoints["items"]
        } - {item["key"] for item in normalized["items"]}
        if removed_keys:
            reasons = _endpoint_deletion_conflicts(removed_keys, before_endpoints, adapter)
            if reasons:
                raise ValueError("拒绝删除接入点：" + "；".join(reasons))
    return adapter.app_config.update_app_setting_as_user(
        section=section,
        value=normalized,
        expected_version=expected_version,
        actor_user_id=actor_user_id,
    )


def _endpoint_deletion_conflicts(
    removed_keys: set[str],
    before_endpoints: Mapping[str, Any],
    adapter: Any,
) -> list[str]:
    reasons: list[str] = []
    if before_endpoints["default"] in removed_keys:
        reasons.append(
            f"「{before_endpoints['default']}」是当前默认接入点，"
            "请先把 default 切换到其他接入点"
        )
    llm_models_row = adapter.app_config.fetch_setting("llm_models")
    if llm_models_row is not None:
        steps = validate_llm_models(llm_models_row["value"])["steps"]
        for removed_key in sorted(removed_keys):
            referencing = [
                LLM_STEP_LABELS[step]
                for step in LLM_STEPS
                if steps[step]["endpoint"] == removed_key
            ]
            if referencing:
                reasons.append(
                    f"「{removed_key}」正被以下步骤引用：{'、'.join(referencing)}"
                )
    return reasons


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
    actor: ConsoleUser,
) -> dict[str, Any]:
    actor_user_id = _require_actor(actor)
    parsed = parse_account(source, text)
    adapter = get_adapter()
    created = adapter.app_config.create_crawl_account_as_user(
        **parsed,
        display_name=None,
        actor_user_id=actor_user_id,
    )
    _resolve_and_record_account_name(adapter, created, timeout=5.0)
    return _account_payload(adapter, str(created["id"]), str(created["source"]))


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
    created = get_adapter().app_config.create_crawl_accounts_as_user(
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
    enabled: Optional[bool],
    set_enabled: bool,
    actor: ConsoleUser,
) -> dict[str, Any]:
    if not set_enabled:
        raise ValueError("至少提交一个可修改字段")
    return get_adapter().app_config.update_crawl_account_as_user(
        account_id=account_id,
        enabled=enabled,
        set_enabled=set_enabled,
        actor_user_id=_require_actor(actor),
    )


def _account_payload(adapter: Any, account_id: str, source: str) -> dict[str, Any]:
    for row in adapter.app_config.fetch_accounts(source):
        if str(row["id"]) == account_id:
            return row
    raise KeyError(account_id)


def _stored_account_name(adapter: Any, account: Mapping[str, Any]) -> Optional[str]:
    return adapter.app_config.fetch_latest_account_article_source(
        source=str(account["source"]),
        normalized_identifier=str(account["normalized_identifier"]),
    )


def _resolve_account_name_with_fallback(
    adapter: Any,
    account: Mapping[str, Any],
    *,
    timeout: float,
) -> str:
    source = str(account["source"])
    if source == "toutiao":
        stored = _stored_account_name(adapter, account)
        if stored:
            return stored[:200]
    try:
        return resolve_account_name(
            source,
            normalized_identifier=str(account["normalized_identifier"]),
            profile_url=str(account["profile_url"]),
            timeout=min(8.0, timeout),
        )
    except AccountNameUnavailable:
        if source == "tencent":
            stored = _stored_account_name(adapter, account)
            if stored:
                return stored[:200]
        raise


def _resolve_and_record_account_name(
    adapter: Any,
    account: Mapping[str, Any],
    *,
    timeout: float,
) -> tuple[str, Optional[str]]:
    try:
        name = _resolve_account_name_with_fallback(
            adapter,
            account,
            timeout=timeout,
        )
    except Exception as exc:
        if isinstance(exc, AccountNameUnavailable):
            error = str(exc) or "未能解析账号名"
        else:
            error = "解析账号名失败"
        error = error[:200]
        adapter.app_config.record_crawl_account_name_failure(
            account_id=str(account["id"]),
            error=error,
        )
        return "failed", error
    status = "unchanged" if name == account.get("display_name") else "resolved"
    adapter.app_config.record_crawl_account_name_success(
        account_id=str(account["id"]),
        display_name=name,
    )
    return status, None


def refresh_account_names(account_ids: list[str]) -> list[dict[str, Any]]:
    adapter = get_adapter()
    deadline = time.monotonic() + 30.0
    items: list[dict[str, Any]] = []
    for index, account_id in enumerate(account_ids):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            items.extend(
                {
                    "id": skipped_id,
                    "status": "skipped",
                    "account": None,
                    "error": "名称刷新已达到 30 秒预算",
                }
                for skipped_id in account_ids[index:]
            )
            break
        account = adapter.app_config.fetch_account(account_id)
        if account is None:
            items.append(
                {
                    "id": account_id,
                    "status": "failed",
                    "account": None,
                    "error": "账号不存在",
                }
            )
            continue
        status, error = _resolve_and_record_account_name(
            adapter,
            account,
            timeout=min(8.0, remaining),
        )
        items.append(
            {
                "id": account_id,
                "status": status,
                "account": (
                    _account_payload(adapter, account_id, str(account["source"]))
                    if status != "failed"
                    else None
                ),
                "error": error,
            }
        )
    return items


def delete_account(account_id: str, *, actor: ConsoleUser) -> dict[str, Any]:
    _require_actor(actor)
    return get_adapter().app_config.delete_crawl_account_as_user(
        account_id=account_id,
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


def _read_wordlist_source(path: Path, *, source_label: str) -> str:
    if not path.exists():
        raise ValueError(f"{source_label}：来源文件不存在（生产环境两个 gitignored 文件应当存在，请检查路径配置）")
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"{source_label}：文件无法读取（{exc}）") from exc


def _parse_bonus_rules_strict(raw_text: str, *, source_label: str) -> list[dict[str, Any]]:
    """Parse the keyword-bonus dict without silently dropping any entry."""

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_label}：无法解析为 JSON（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{source_label}：必须是 JSON 对象（关键词到分值的映射）")
    items: list[dict[str, Any]] = []
    for keyword, bonus in data.items():
        if not keyword.strip():
            raise ValueError(f"{source_label}：存在空白关键词")
        if isinstance(bonus, bool) or not isinstance(bonus, int):
            raise ValueError(
                f"{source_label}：关键词「{keyword}」的分值必须是整数，当前为 {bonus!r}"
            )
        items.append({"keyword": keyword.strip(), "bonus": bonus})
    return items


def _parse_wordlist_strict(raw_text: str, *, source_label: str) -> list[str]:
    items: list[str] = []
    for line_number, raw in enumerate(raw_text.splitlines(), start=1):
        token = raw.strip()
        if not token or token.startswith("#"):
            continue
        items.append(token)
    return items


def _parse_source_aliases_strict(raw_text: str, *, source_label: str) -> dict[str, Any]:
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source_label}：无法解析为 JSON（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{source_label}：必须是 JSON 对象（suffixes + aliases）")
    suffixes = data.get("suffixes")
    aliases = data.get("aliases")
    if not isinstance(suffixes, list):
        raise ValueError(f"{source_label}：suffixes 必须是有序列表")
    if not isinstance(aliases, dict):
        raise ValueError(f"{source_label}：aliases 必须是对象")
    for suffix in suffixes:
        if not isinstance(suffix, str) or not suffix:
            raise ValueError(f"{source_label}：suffixes 存在非字符串或空项")
    for key, target in aliases.items():
        if not isinstance(key, str) or not key or not isinstance(target, str) or not target:
            raise ValueError(f"{source_label}：aliases 的键和值都必须是非空字符串")
    return {"suffixes": suffixes, "aliases": aliases}


def _load_bonus_rules_source(
    root: Path,
) -> tuple[list[dict[str, Any]], str]:
    env_value = os.getenv("SCORE_KEYWORD_BONUSES")
    if env_value is not None and env_value.strip():
        source_label = "环境变量 SCORE_KEYWORD_BONUSES"
        return (
            _parse_bonus_rules_strict(env_value, source_label=source_label),
            source_label,
        )
    path = root / "config" / "score_keyword_bonuses.json"
    source_label = str(path)
    return (
        _parse_bonus_rules_strict(
            _read_wordlist_source(path, source_label=source_label),
            source_label=source_label,
        ),
        source_label,
    )


def _load_wordlist_source(
    root: Path,
    *,
    relative_path: str,
    source_label: str,
) -> tuple[list[str], str]:
    path = root / relative_path
    return (
        _parse_wordlist_strict(
            _read_wordlist_source(path, source_label=source_label),
            source_label=source_label,
        ),
        source_label,
    )


def _load_source_aliases_source(root: Path) -> tuple[dict[str, Any], str]:
    path = root / "config" / "source_aliases.json"
    source_label = str(path)
    return (
        _parse_source_aliases_strict(
            _read_wordlist_source(path, source_label=source_label),
            source_label=source_label,
        ),
        source_label,
    )


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
    sections: dict[str, Any] = {
        "llm_models": validate_section("llm_models", _legacy_models()),
        "crawl_sources": validate_crawl_sources(
            normalized_sources,
            allow_daily=True,
        ),
    }
    section_sources: dict[str, str] = {}
    wordlist_errors: list[dict[str, Any]] = []
    wordlist_loaders = {
        "score_keyword_bonuses": _load_bonus_rules_source,
        "education_keywords": lambda root_dir: _load_wordlist_source(
            root_dir,
            relative_path="config/education_keywords.txt",
            source_label=str(root_dir / "config" / "education_keywords.txt"),
        ),
        "beijing_keywords": lambda root_dir: _load_wordlist_source(
            root_dir,
            relative_path="config/beijing_keywords.txt",
            source_label=str(root_dir / "config" / "beijing_keywords.txt"),
        ),
        "source_aliases": _load_source_aliases_source,
    }
    for section, loader in wordlist_loaders.items():
        try:
            value, source_label = loader(repository_root)
        except ValueError as exc:
            wordlist_errors.append({"section": section, "detail": str(exc)})
            continue
        try:
            sections[section] = validate_section(section, value)
        except ValueError as exc:
            wordlist_errors.append(
                {"section": section, "detail": f"{source_label}：{exc}"}
            )
            continue
        section_sources[section] = source_label

    sections_status: dict[str, dict[str, Any]] = {}
    db_state_known = not wordlist_errors and not has_parse_errors
    existing_sections: set[str] = set()
    if db_state_known:
        rows = get_adapter().app_config.fetch_settings()
        existing_sections = {str(row["section"]) for row in rows}
    for section, value in sections.items():
        if not db_state_known:
            status = "unresolved"
        elif section in existing_sections:
            status = "exists_skip"
        else:
            status = "write"
        sections_status[section] = {
            "status": status,
            "source": section_sources.get(section),
            "item_count": (
                len(value["suffixes"]) + len(value["aliases"])
                if section == "source_aliases" and isinstance(value, Mapping)
                else len(value)
            ),
        }
    return {
        "sections": sections,
        "sections_status": sections_status,
        "account_summary": account_summary,
        "accounts": accounts,
        "daily_only_sources": daily_sources,
        "has_parse_errors": has_parse_errors,
        "wordlist_errors": wordlist_errors,
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
    if preview["wordlist_errors"]:
        details = "；".join(item["detail"] for item in preview["wordlist_errors"])
        raise ValueError(f"词表配置来源无法严格解析，拒绝导入：{details}")
    report = get_adapter().import_app_config_missing(
        sections=preview["sections"],
        accounts=preview["accounts"],
    )
    return {**preview, **report}


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
    "refresh_account_names",
    "test_llm_model",
    "update_account",
    "update_setting",
]
