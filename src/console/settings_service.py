from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import urlsplit

from src.adapters import (
    external_filter_model,
    llm_beijing_gate,
    llm_duplicate_review,
    llm_prompt_optimizer,
    llm_scoring,
    llm_source,
    llm_summary,
    sentiment_classifier,
)
from src.adapters.account_profiles import (
    AccountNameUnavailable,
    MAX_ACCOUNT_TIMEOUT_SECONDS,
    resolve_account_name,
)
from src.adapters.db_postgres_core import get_adapter
from src.adapters.http_21jingji import parse_channel_input as parse_21jingji_channel
from src.adapters.http_beijinghao import parse_column_input
from src.adapters.http_btime import parse_uid_input
from src.adapters.http_chinadaily import parse_channel_input as parse_chinadaily_channel
from src.adapters.http_qianlong import parse_channel_input as parse_qianlong_channel
from src.adapters.http_tencent import parse_author_input as parse_tencent_author
from src.adapters.http_toutiao import (
    PROFILE_NAME_TIMEOUT_SECONDS,
    parse_author_input as parse_toutiao_author,
)
from src.adapters.http_xinjinghao import (
    parse_account_input as parse_xinjinghao_account,
)
from src.adapters.llm_chat import post_chat_completion
from src.adapters.llm_endpoint import (
    LLMEndpointKeyMissingError,
    ResolvedLLMEndpoint,
    api_key_env_configured,
    resolve_endpoint,
)
from src.business_config import (
    ACCOUNT_SOURCES,
    LLM_API_KEY_ENV_PATTERN,
    LLMEndpointConfig,
    LLM_STEPS,
    LLM_STEP_LABELS,
    SOURCE_BY_KEY,
    SOURCE_CATALOG,
    endpoint_host_allowed,
    normalize_source_key,
    normalize_source_list,
    resolve_llm_endpoints,
    resolve_llm_steps,
    validate_crawl_sources,
    validate_llm_endpoints,
    validate_llm_models,
    validate_section,
)
from src.config import BGE_EMBEDDING_MODEL, get_settings, load_environment
from src.console.auth_service import ConsoleUser
from src.console.source_list_pages import list_pages_for_source


logger = logging.getLogger(__name__)


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


def _configured_api_key_envs() -> list[str]:
    """服务器当前已配置、且名字合法的 Key 环境变量名，按字母排序。

    只给名字，绝不给值或任何派生信息（长度、前缀都不给）。前端拿这份
    名单对任意变量名（包括还没保存的新接入点）判断配没配。判定与
    resolve_endpoint 实际取 Key 完全一致：值非空才算已配置，空串算未配置。"""

    return sorted(
        name
        for name in os.environ
        if LLM_API_KEY_ENV_PATTERN.fullmatch(name) and api_key_env_configured(name)
    )


def _endpoints_payload(settings: Any, sections: Mapping[str, Any]) -> dict[str, Any]:
    # 读取已保存配置只做结构校验（require_host_allowed=False）：白名单收紧后
    # 库里可能有接入点指向白名单外的域名，设置页必须照常打开并把问题标出来
    # （host_allowed=False），让用户能在控制台里修，而不是整页打不开。
    normalized = None
    row = sections.get("llm_endpoints")
    if row is not None:
        normalized = validate_llm_endpoints(row["value"], require_host_allowed=False)
    return {
        "default": normalized["default"] if normalized else None,
        "allowed_hosts": list(settings.llm_allowed_hosts),
        "configured_api_key_envs": _configured_api_key_envs(),
        "items": [
            {
                "key": item["key"],
                "label": item["label"],
                "base_url": item["base_url"],
                "api_key_env": item["api_key_env"],
                "api_style": item["api_style"],
                "temperature_override": item["temperature_override"],
                "default_model": item["default_model"],
                "host_allowed": endpoint_host_allowed(
                    item["base_url"],
                    allowed_hosts=settings.llm_allowed_hosts,
                ),
            }
            for item in (normalized["items"] if normalized else [])
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
                # 列表页链接直接取自各爬虫 adapter 的常量（见 source_list_pages），
                # 只有每小时、且不需要账号的来源才有；其余来源给空数组
                "list_pages": (
                    []
                    if item.requires_accounts or item.daily_only
                    else list_pages_for_source(item.key)
                ),
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
    # 读取已保存配置只做结构校验：白名单外的接入点仍可被「测试」按钮引用，
    # 由探针在发请求前拦下并给出可操作的报错
    items, default_key = resolve_llm_endpoints(row["value"], require_host_allowed=False)
    resolved = endpoint_key or default_key
    definition = items.get(resolved)
    if definition is None:
        raise ValueError(f"接入点不存在：{resolved}")
    return definition


# 每个步骤请求里 model/messages/max_tokens 之外的固定参数，与各 adapter
# 的真实请求共用同一份定义（request_extras）：真实请求用它拼请求体，
# 步骤级实测取同一份值，两边永远一致。接入点级探针（无步骤可依）不走
# 这里，用简单探针。
_STEP_REQUEST_EXTRAS = {
    "summary": llm_summary.request_extras,
    "source": llm_source.request_extras,
    "sentiment": sentiment_classifier.request_extras,
    "scoring": llm_scoring.request_extras,
    "external_filter": external_filter_model.request_extras,
    "beijing_gate": llm_beijing_gate.request_extras,
    "duplicate_review": llm_duplicate_review.request_extras,
    "prompt_optimizer": llm_prompt_optimizer.request_extras,
}


def _probe_model(
    *,
    model: str,
    reasoning: bool,
    endpoint_definition: LLMEndpointConfig,
    operation: str,
    step: Optional[str] = None,
) -> dict[str, Any]:
    """对「接入点定义 + 模型 + reasoning」组合发一次最小真实请求。

    探针直接接收接入点定义，不拿 key 去数据库查：保存 llm_endpoints 时
    库里还是旧值，查库会让新增接入点的实测报「接入点不存在」，改连接
    信息时实测的也是旧地址/旧 Key——保存时的校验必须作用于待保存的值。
    逐步「测试」按钮经 test_llm_model 传入库中已保存的定义（按钮只引用
    已保存接入点）。operation 只用于请求日志归属，不参与校验。

    step 给定时（保存时的步骤级实测、步骤「测试」按钮），探针带上该步骤
    真实请求的固定参数（response_format、temperature，来自该 adapter 的
    request_extras——真实请求也用它拼请求体，两边只有这一份定义）——
    有的服务商不接受这些参数，只发简单探针会「实测通过、跑到这一步报
    400」。接入点级实测（暂时没有步骤在用的接入点）没有步骤可依，
    不传 step。"""

    settings = get_settings()
    try:
        resolved = resolve_endpoint(endpoint_definition)
    except LLMEndpointKeyMissingError:
        return {
            "success": False,
            "elapsed_ms": 0,
            "error": (
                f"缺少 API Key：请先在服务器的 .env.local 里配置 "
                f"{endpoint_definition.api_key_env}，再重新保存"
            ),
        }
    blocked_host = _blocked_endpoint_host(resolved, settings)
    if blocked_host:
        return {
            "success": False,
            "elapsed_ms": 0,
            "error": (
                f"接入点主机 {blocked_host} 不在允许列表内：请在服务器 .env.local "
                "的 LLM_ALLOWED_HOSTS 里追加该域名（不用重复写默认的），"
                "或修改该接入点的地址后再保存。"
            ),
        }
    extras: Mapping[str, Any] = (
        _STEP_REQUEST_EXTRAS[step](resolved) if step is not None else {}
    )
    reasoning_limit = settings.llm_reasoning_max_tokens or 0
    max_tokens = max(2048, reasoning_limit * 2) if reasoning else 8
    request_timeout = (
        max(120, settings.llm_scoring_timeout)
        if reasoning
        else min(10, settings.llm_scoring_timeout)
    )
    request_budget = max(180, request_timeout + 30) if reasoning else 12
    # JSON 模式通常要求提示里出现 JSON 字样，探针消息据此调整
    probe_message = (
        '连通性测试：请只回复 JSON {"ok": true}'
        if extras.get("response_format") is not None
        else "只回复 OK"
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": probe_message}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    payload.update(extras)
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
            operation=operation,
            model=model,
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


def _blocked_endpoint_host(
    resolved: ResolvedLLMEndpoint,
    settings: Any,
) -> Optional[str]:
    """接入点主机不在白名单内时返回主机名；实测与「测试」按钮据此在
    发请求前拦下（传输层另有同样的一道检查，这里提前给出可操作的报错）。"""

    host = (urlsplit(resolved.chat_url).hostname or "").strip().lower()
    allowed = {
        item.strip().lower()
        for item in settings.llm_allowed_hosts
        if item and item.strip()
    }
    if host and host not in allowed:
        return host
    return None


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
    return _probe_model(
        model=normalized_model,
        reasoning=reasoning,
        endpoint_definition=_endpoint_definition(endpoint),
        operation=f"settings_model_test:{step}",
        step=step,
    )


def _require_llm_endpoints(adapter: Any) -> tuple[dict[str, LLMEndpointConfig], str]:
    row = adapter.app_config.fetch_setting("llm_endpoints")
    if row is None:
        raise ValueError("接入点配置未初始化：数据库缺少 llm_endpoints 分区")
    # 保存 llm_models 时解析的是**已保存**接入点：只做结构校验，不因某个
    # 接入点的主机不在白名单而拒绝整次保存——用户可能正是在把步骤从它
    # 身上改走；白名单问题在设置页负载里以 host_allowed 标出
    return resolve_llm_endpoints(row["value"], require_host_allowed=False)


def _resolved_step_combinations(
    models_value: Optional[Any],
    endpoint_items: Mapping[str, LLMEndpointConfig],
    default_endpoint: str,
) -> dict[str, tuple[str, str, bool]]:
    """每一步最终使用的「接入点 key + 模型 + reasoning」。

    llm_models 分区缺失（全新部署尚未 import-settings）时没有步骤，返回空表。"""

    if models_value is None:
        return {}
    steps = resolve_llm_steps(
        models_value,
        endpoints=endpoint_items,
        default_endpoint=default_endpoint,
    )
    return {
        step: (
            steps[step].endpoint or default_endpoint,
            steps[step].model,
            steps[step].reasoning,
        )
        for step in LLM_STEPS
    }


def _endpoint_connection_signature(endpoint: LLMEndpointConfig) -> tuple[str, str, str]:
    return (endpoint.base_url, endpoint.api_key_env, endpoint.api_style)


def _run_save_time_model_tests(
    *,
    models_before: Optional[Any],
    models_after: Optional[Any],
    endpoints_before: Mapping[str, LLMEndpointConfig],
    default_endpoint_before: str,
    endpoints_after: Mapping[str, LLMEndpointConfig],
    default_endpoint_after: str,
) -> None:
    """保存 llm_models / llm_endpoints 前的统一模型测试。

    每一步分别比较保存前、保存后最终使用的「接入点 + 模型 + reasoning」：
    三项变了，或所用接入点的连接信息（base_url / api_key_env / api_style）
    变了，就测保存后的组合，相同组合只测一次。接入点层面另测：新增的接入点
    或 default_model 改了的接入点，即使暂时没有步骤在用，也用 reasoning 关
    测一次它的默认模型——以后哪一步切过去也不会拿到一个坏的默认值。
    任一测试失败抛 ValueError 拒绝整次保存，数据库不变。
    两类探针都用**将要保存的**接入点定义（endpoints_after）连接——
    库里还是旧值，查库会让新增接入点实测报「不存在」、改连接信息实测
    旧地址/旧 Key；保存 llm_models 时 endpoints_after 本来就是库里的
    已保存值，与 endpoints 保存共用同一条传入定义的路径。"""

    before_combinations = _resolved_step_combinations(
        models_before, endpoints_before, default_endpoint_before
    )
    after_combinations = _resolved_step_combinations(
        models_after, endpoints_after, default_endpoint_after
    )
    tested: set[tuple[str, str, bool]] = set()

    def run_step_test(step: str, combination: tuple[str, str, bool]) -> None:
        endpoint_key, model, reasoning = combination
        if combination in tested:
            return
        tested.add(combination)
        outcome = _probe_model(
            model=model,
            reasoning=reasoning,
            endpoint_definition=endpoints_after[endpoint_key],
            operation=f"settings_model_test:{step}",
            step=step,
        )
        if not outcome["success"]:
            raise ValueError(
                f"{LLM_STEP_LABELS[step]}模型测试失败：{outcome['error']}"
            )

    for step in LLM_STEPS:
        before = before_combinations.get(step)
        after = after_combinations.get(step)
        if after is None:
            continue
        if before == after:
            # 组合未变也可能要测：所用接入点的连接信息变了
            endpoint_before = endpoints_before.get(after[0])
            if endpoint_before is None or (
                _endpoint_connection_signature(endpoint_before)
                == _endpoint_connection_signature(endpoints_after[after[0]])
            ):
                continue
        run_step_test(step, after)

    # 接入点默认模型测试（reasoning 关）：新增接入点、default_model 改了
    for key, item in endpoints_after.items():
        before_item = endpoints_before.get(key)
        if before_item is not None and before_item.default_model == item.default_model:
            continue
        combination = (key, item.default_model, False)
        if combination in tested:
            continue
        tested.add(combination)
        outcome = _probe_model(
            model=item.default_model,
            reasoning=False,
            endpoint_definition=item,
            operation=f"settings_model_test:{key}",
        )
        if not outcome["success"]:
            raise ValueError(
                f"接入点「{item.label}」的默认模型 {item.default_model} "
                f"测试失败：{outcome['error']}"
            )


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
        before_endpoints_items, before_default = _require_llm_endpoints(adapter)
        after_steps = resolve_llm_steps(
            normalized,
            endpoints=before_endpoints_items,
            default_endpoint=before_default,
        )
        for step in LLM_STEPS:
            endpoint_ref = after_steps[step].endpoint
            if endpoint_ref is not None and endpoint_ref not in before_endpoints_items:
                raise ValueError(
                    f"{LLM_STEP_LABELS[step]}引用了不存在的接入点：{endpoint_ref}"
                )
        _run_save_time_model_tests(
            models_before=current["value"],
            models_after=normalized,
            endpoints_before=before_endpoints_items,
            default_endpoint_before=before_default,
            endpoints_after=before_endpoints_items,
            default_endpoint_after=before_default,
        )
    elif section == "llm_endpoints":
        # 读取保存前的接入点只做结构校验：库里可能已有指向白名单外域名的
        # 接入点，用户这次保存正是要改掉或删掉它，读路径不能先拦住
        before_endpoints_items, before_default = resolve_llm_endpoints(
            current["value"],
            require_host_allowed=False,
        )
        removed_keys = set(before_endpoints_items) - {
            item["key"] for item in normalized["items"]
        }
        if removed_keys:
            reasons = _endpoint_deletion_conflicts(
                removed_keys,
                validate_llm_endpoints(current["value"], require_host_allowed=False),
                adapter,
            )
            if reasons:
                raise ValueError("拒绝删除接入点：" + "；".join(reasons))
        models_row = adapter.app_config.fetch_setting("llm_models")
        after_endpoints_items, after_default = resolve_llm_endpoints(normalized)
        _run_save_time_model_tests(
            models_before=models_row["value"] if models_row else None,
            models_after=models_row["value"] if models_row else None,
            endpoints_before=before_endpoints_items,
            default_endpoint_before=before_default,
            endpoints_after=after_endpoints_items,
            default_endpoint_after=after_default,
        )
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
    if normalized_source == "xinjinghao":
        def parse(raw: str) -> dict[str, str]:
            item = parse_xinjinghao_account(raw)
            return {
                "normalized_identifier": item.xjh_id,
                "profile_url": item.page_url,
            }

        return parse
    if normalized_source == "21jingji":
        def parse(raw: str) -> dict[str, str]:
            item = parse_21jingji_channel(raw)
            return {
                "normalized_identifier": item.url,
                "profile_url": item.url,
            }

        return parse
    if normalized_source == "chinadaily":
        def parse(raw: str) -> dict[str, str]:
            item = parse_chinadaily_channel(raw)
            return {
                "normalized_identifier": item.url,
                "profile_url": item.url,
            }

        return parse
    if normalized_source == "qianlong":
        def parse(raw: str) -> dict[str, str]:
            item = parse_qianlong_channel(raw)
            return {
                "normalized_identifier": item.url,
                "profile_url": item.url,
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
    # 创建请求不做名称解析（头条要起无头浏览器，同步解析会把添加卡住十几秒）：
    # 前端收到 201 后立即对这一条调 refresh-names，芯片先以「解析中」示人。
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
        if account.get("display_name"):
            # 已有名称说明此前解析成功过；网页解析每次要起无头浏览器，
            # 只为复核一个现成名不值得，视为无需变更。
            return str(account["display_name"])
    try:
        return resolve_account_name(
            source,
            normalized_identifier=str(account["normalized_identifier"]),
            profile_url=str(account["profile_url"]),
            timeout=timeout,
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


def _account_resolve_timeout(source: str, remaining: float) -> float:
    """单个账号的解析预算：头条网页解析要起浏览器，按它的上限给；
    其他来源走纯 HTTP，钳在 8 秒。都不越过本轮刷新的剩余预算。"""
    cap = (
        PROFILE_NAME_TIMEOUT_SECONDS
        if normalize_source_key(source) == "toutiao"
        else MAX_ACCOUNT_TIMEOUT_SECONDS
    )
    return min(cap, remaining)


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
            timeout=_account_resolve_timeout(
                str(account["source"]),
                remaining,
            ),
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


# 旧环境变量全部缺失时的兜底模型，与全新部署迁移
# （20261106150000_move_default_model_into_endpoints.sql）给种子接入点写的
# default_model 是同一份值；两边必须保持一致。
LLM_FALLBACK_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
# 迁移种子里唯一接入点的 key；llm_endpoints 分区读不到时（库暂不可达或
# 迁移未跑）按种子形态兜底，仅影响预览展示，unresolved 状态不会写入。
LEGACY_FALLBACK_ENDPOINT_KEY = "openrouter"


def _legacy_endpoint_reference() -> tuple[str, str]:
    """默认接入点 key 与它的 default_model，供 _legacy_models 组装新结构。"""

    try:
        row = get_adapter().app_config.fetch_setting("llm_endpoints")
    except Exception:
        # 降级路径：预览要能在库不可达时仍展示解析结果（标记 unresolved，
        # 不会写入），此时退回迁移种子的同一份兜底值。
        row = None
    if row is None:
        return LEGACY_FALLBACK_ENDPOINT_KEY, LLM_FALLBACK_DEFAULT_MODEL
    items, default_key = resolve_llm_endpoints(row["value"], require_host_allowed=False)
    return default_key, items[default_key].default_model


def _legacy_models(*, default_endpoint: str, default_model: str) -> dict[str, Any]:
    """从旧环境变量拼装 llm_models 分区（新结构：无顶层 default）。

    等于默认接入点 default_model 的步骤写双 null（跟随默认接入点的默认
    模型），其余步骤显式绑定默认接入点 key + 该模型——继承链的基准仍是
    LLM_MODEL 环境变量（旧语义），但「写成 null」的判据是接入点的
    default_model，保证解析结果与旧结构一致。"""

    default = os.getenv("LLM_MODEL") or LLM_FALLBACK_DEFAULT_MODEL
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
        "steps": {
            step: {
                "model": None if model == default_model else model,
                "reasoning": reasoning[step],
                "endpoint": None if model == default_model else default_endpoint,
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
    # 默认接入点引用的兜底值：库可达时才真正读取（见下方 db_state_known 分支），
    # 解析有错误时不碰库，预览 unresolved 不会写入，兜底值只影响展示。
    legacy_endpoint_key, legacy_default_model = (
        LEGACY_FALLBACK_ENDPOINT_KEY,
        LLM_FALLBACK_DEFAULT_MODEL,
    )
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
        "llm_models": validate_section(
            "llm_models",
            _legacy_models(
                default_endpoint=legacy_endpoint_key,
                default_model=legacy_default_model,
            ),
        ),
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
        # 库可达时才读默认接入点引用：解析有错误时不碰库（预览 unresolved
        # 不写入），引用值保持上面的迁移种子兜底。
        legacy_endpoint_key, legacy_default_model = _legacy_endpoint_reference()
        sections["llm_models"] = validate_section(
            "llm_models",
            _legacy_models(
                default_endpoint=legacy_endpoint_key,
                default_model=legacy_default_model,
            ),
        )
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
