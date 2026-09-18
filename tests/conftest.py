from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

from src.business_config import (
    BusinessConfig,
    LLMEndpointConfig,
    LLMStepConfig,
    LLM_STEPS,
    ScoreKeywordBonus,
    business_config_context,
)
from src.domain import SourceAliasRules

OPENROUTER_ENDPOINT = LLMEndpointConfig(
    key="openrouter",
    label="OpenRouter",
    base_url="https://openrouter.ai/api/v1",
    api_key_env="LLM_API_KEY",
    api_style="openrouter",
    temperature_override=None,
)
DEEPSEEK_ENDPOINT = LLMEndpointConfig(
    key="deepseek",
    label="DeepSeek",
    base_url="https://api.deepseek.com",
    api_key_env="LLM_DEEPSEEK_API_KEY",
    api_style="thinking",
    temperature_override=None,
)


def make_endpoint_config(
    *,
    endpoints: tuple[LLMEndpointConfig, ...] = (OPENROUTER_ENDPOINT,),
    default_endpoint: str = "openrouter",
    step_endpoints: dict[str, str | None] | None = None,
    score_keyword_bonuses: tuple[ScoreKeywordBonus, ...] = (),
    education_keywords: tuple[str, ...] = ("教育",),
    beijing_keywords: tuple[str, ...] = ("北京",),
    source_aliases: SourceAliasRules | None = None,
) -> BusinessConfig:
    step_endpoints = step_endpoints or {}
    return BusinessConfig(
        llm_steps={
            step: LLMStepConfig(
                model=f"{step}-model",
                reasoning=True,
                endpoint=step_endpoints.get(step),
            )
            for step in LLM_STEPS
        },
        crawl_sources=("toutiao",),
        accounts={},
        versions={},
        llm_endpoints={item.key: item for item in endpoints},
        default_endpoint=default_endpoint,
        score_keyword_bonuses=score_keyword_bonuses,
        education_keywords=education_keywords,
        beijing_keywords=beijing_keywords,
        source_aliases=source_aliases or SourceAliasRules(),
    )


@pytest.fixture
def openrouter_context(monkeypatch: pytest.MonkeyPatch):
    """Freeze business config to the legacy shape: one OpenRouter endpoint,
    every step following the default. Keeps adapter tests off the database."""

    @contextmanager
    def _factory(**kwargs) -> Iterator[BusinessConfig]:
        config = make_endpoint_config(**kwargs)
        with business_config_context(config):
            yield config

    return _factory


@pytest.fixture
def openrouter_endpoint_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """OpenRouter-only business config plus the API key env, for adapter
    tests whose real call path resolves an endpoint."""

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    with business_config_context(make_endpoint_config()):
        yield
