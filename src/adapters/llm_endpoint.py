from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, MutableMapping, Optional

from src.adapters.llm_chat import apply_reasoning_config, build_headers
from src.business_config import (
    API_STYLE_OPENROUTER,
    API_STYLE_THINKING,
    BusinessConfig,
    LLMEndpointConfig,
    get_business_config,
)
from src.config import Settings, get_settings

__all__ = [
    "LLMEndpointKeyMissingError",
    "ResolvedLLMEndpoint",
    "api_key_env_configured",
    "chat_completions_url",
    "resolve_endpoint",
    "resolve_llm_endpoint",
]


class LLMEndpointKeyMissingError(RuntimeError):
    """Raised when the endpoint's api_key_env is not present in the environment."""


@dataclass(frozen=True)
class ResolvedLLMEndpoint:
    """Everything an adapter needs to reach one LLM endpoint.

    Adapters go through this object for the chat URL, the Authorization
    headers, and the api_style-specific reasoning/temperature request-body
    options, so the seven call sites never re-assemble them by hand.
    """

    key: str
    label: str
    base_url: str
    chat_url: str
    api_key: str
    api_key_env: str
    api_style: str
    temperature_override: Optional[float]
    referer: Optional[str]
    title: Optional[str]

    def headers(self) -> dict[str, str]:
        return build_headers(
            api_key=self.api_key,
            referer=self.referer,
            title=self.title,
        )

    def finalize_payload(
        self,
        payload: MutableMapping[str, Any],
        *,
        settings: Settings,
        reasoning_enabled: bool,
    ) -> None:
        """Apply api_style-specific reasoning and the temperature override.

        thinking-style providers keep their own default thinking behaviour
        unless it is explicitly disabled, so the disabled case sends
        ``{"thinking": {"type": "disabled"}}`` instead of omitting the field.
        """

        if self.api_style == API_STYLE_THINKING:
            if reasoning_enabled:
                payload["thinking"] = {"type": "enabled"}
                if settings.llm_reasoning_effort:
                    payload["reasoning_effort"] = settings.llm_reasoning_effort
            else:
                payload["thinking"] = {"type": "disabled"}
        else:
            apply_reasoning_config(
                payload,
                settings=settings,
                enabled=reasoning_enabled,
            )
        if self.temperature_override is not None:
            payload["temperature"] = self.temperature_override


def chat_completions_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def api_key_env_configured(env_name: str) -> bool:
    """该环境变量是否已配置出可用的 Key：值非空才算已配置。

    与 resolve_endpoint 实际取 Key 的判定是同一个「真值判断」，两处共用
    这条语义：设置页的「已配置」名单按它过滤，保存时的实测按它判缺 Key。
    空串算未配置——显示已配置但保存被拒是最伤信任的不一致。"""

    return bool(os.getenv(env_name))


def resolve_endpoint(definition: LLMEndpointConfig) -> ResolvedLLMEndpoint:
    settings = get_settings()
    api_key = os.getenv(definition.api_key_env)
    if not api_key:
        raise LLMEndpointKeyMissingError(
            f"Missing LLM API key for endpoint '{definition.label}' "
            f"(set {definition.api_key_env})"
        )
    is_openrouter = definition.api_style == API_STYLE_OPENROUTER
    return ResolvedLLMEndpoint(
        key=definition.key,
        label=definition.label,
        base_url=definition.base_url,
        chat_url=chat_completions_url(definition.base_url),
        api_key=api_key,
        api_key_env=definition.api_key_env,
        api_style=definition.api_style,
        temperature_override=definition.temperature_override,
        referer=settings.llm_api_http_referer if is_openrouter else None,
        title=settings.llm_api_title if is_openrouter else None,
    )


def resolve_llm_endpoint(
    step: str,
    *,
    endpoint_key: Optional[str] = None,
    config: Optional[BusinessConfig] = None,
) -> ResolvedLLMEndpoint:
    """Resolve the endpoint a step should call.

    Without ``endpoint_key`` the step follows its own configured endpoint,
    falling back to the default endpoint when the step has none.
    """

    business_config = config if config is not None else get_business_config()
    if endpoint_key is None:
        definition = business_config.endpoint_for_step(step)
    else:
        definition = business_config.endpoint_by_key(endpoint_key)
    return resolve_endpoint(definition)
