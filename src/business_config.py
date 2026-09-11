from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence


LLM_STEPS = (
    "summary",
    "source",
    "sentiment",
    "scoring",
    "external_filter",
    "beijing_gate",
    "duplicate_review",
)
LLM_STEP_LABELS = {
    "summary": "摘要生成",
    "source": "来源识别",
    "sentiment": "情感判断",
    "scoring": "相关性评分",
    "external_filter": "重要性过滤",
    "beijing_gate": "京内判定",
    "duplicate_review": "查重复核",
}


@dataclass(frozen=True)
class SourceDefinition:
    key: str
    display_name: str
    daily_only: bool = False
    requires_accounts: bool = False
    aliases: tuple[str, ...] = ()


SOURCE_CATALOG = (
    SourceDefinition("toutiao", "今日头条", requires_accounts=True),
    SourceDefinition("tencent", "腾讯新闻", requires_accounts=True, aliases=("qq",)),
    SourceDefinition("chinanews", "中新网"),
    SourceDefinition("chinanews_xj", "中新网新疆"),
    SourceDefinition("jyb", "中国教育报"),
    SourceDefinition("chinadaily", "中国日报"),
    SourceDefinition("gmw", "光明网"),
    SourceDefinition("qianlong", "千龙网"),
    SourceDefinition("btime", "北京时间", requires_accounts=True),
    SourceDefinition("beijinghao", "北京号", requires_accounts=True),
    SourceDefinition("bjrb", "北京日报", daily_only=True, aliases=("beijingdaily",)),
    SourceDefinition(
        "ldwb",
        "劳动午报",
        daily_only=True,
        aliases=("laodongwubao",),
    ),
)
SOURCE_BY_KEY = {item.key: item for item in SOURCE_CATALOG}
SOURCE_ALIASES = {
    alias: item.key
    for item in SOURCE_CATALOG
    for alias in item.aliases
}
ACCOUNT_SOURCES = tuple(
    item.key for item in SOURCE_CATALOG if item.requires_accounts
)
SETTING_SECTIONS = ("llm_models", "crawl_sources")


class BusinessConfigError(RuntimeError):
    """Raised when database-backed business configuration is missing or invalid."""


@dataclass(frozen=True)
class CrawlAccount:
    id: str
    source: str
    normalized_identifier: str
    original_input: str
    profile_url: str
    display_name: Optional[str]


@dataclass(frozen=True)
class LLMStepConfig:
    model: str
    reasoning: bool


@dataclass(frozen=True)
class BusinessConfig:
    llm_steps: Mapping[str, LLMStepConfig]
    crawl_sources: tuple[str, ...]
    accounts: Mapping[str, tuple[CrawlAccount, ...]]
    versions: Mapping[str, int]

    def model_for(self, step: str) -> str:
        return self.step_config(step).model

    def step_config(self, step: str) -> LLMStepConfig:
        try:
            return self.llm_steps[step]
        except KeyError as exc:
            raise BusinessConfigError(f"缺少模型配置步骤：{step}") from exc

    def with_crawl_sources(self, sources: Sequence[str]) -> "BusinessConfig":
        normalized = normalize_source_list(sources, allow_daily=True)
        return replace(self, crawl_sources=tuple(normalized))

    def snapshot(self) -> dict[str, Any]:
        return {
            "llm_models": {
                step: {
                    "model": config.model,
                    "reasoning": config.reasoning,
                }
                for step, config in self.llm_steps.items()
            },
            "crawl_sources": list(self.crawl_sources),
            "crawl_accounts": {
                source: [item.normalized_identifier for item in items]
                for source, items in self.accounts.items()
            },
            "versions": dict(self.versions),
        }


_CURRENT_CONFIG: ContextVar[Optional[BusinessConfig]] = ContextVar(
    "business_config",
    default=None,
)


def normalize_source_key(value: str) -> str:
    key = str(value or "").strip().lower()
    return SOURCE_ALIASES.get(key, key)


def normalize_source_list(
    values: Sequence[str],
    *,
    allow_daily: bool,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = normalize_source_key(raw)
        if not key:
            raise ValueError("来源不能为空")
        definition = SOURCE_BY_KEY.get(key)
        if definition is None:
            raise ValueError(f"未知来源：{raw}")
        if definition.daily_only and not allow_daily:
            raise ValueError(f"{raw} 仅允许每日单独任务运行，不能加入每小时来源")
        if key in seen:
            raise ValueError(f"来源重复：{raw}")
        seen.add(key)
        normalized.append(key)
    if not normalized:
        raise ValueError("抓取来源列表不能为空")
    return normalized


def validate_llm_models(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("llm_models 必须是对象")
    unexpected = set(value) - {"default", "steps"}
    if unexpected:
        raise ValueError(f"llm_models 含未知字段：{', '.join(sorted(unexpected))}")
    default = value.get("default")
    if not isinstance(default, str) or not default.strip():
        raise ValueError("llm_models.default 必须是非空模型名")
    steps = value.get("steps")
    if not isinstance(steps, Mapping):
        raise ValueError("llm_models.steps 必须是对象")
    missing = [step for step in LLM_STEPS if step not in steps]
    extra = sorted(set(steps) - set(LLM_STEPS))
    if missing:
        raise ValueError(f"llm_models.steps 缺少：{', '.join(missing)}")
    if extra:
        raise ValueError(f"llm_models.steps 含未知步骤：{', '.join(extra)}")
    normalized_steps: dict[str, dict[str, Any]] = {}
    for step in LLM_STEPS:
        step_value = steps[step]
        if not isinstance(step_value, Mapping):
            raise ValueError(f"llm_models.steps.{step} 必须是对象")
        unexpected_step_fields = set(step_value) - {"model", "reasoning"}
        if unexpected_step_fields:
            fields = ", ".join(sorted(unexpected_step_fields))
            raise ValueError(f"llm_models.steps.{step} 含未知字段：{fields}")
        if "model" not in step_value:
            raise ValueError(f"llm_models.steps.{step} 缺少 model")
        if "reasoning" not in step_value:
            raise ValueError(f"llm_models.steps.{step} 缺少 reasoning")
        model = step_value["model"]
        if model is None:
            normalized_model = None
        elif isinstance(model, str) and model.strip():
            normalized_model = model.strip()
        else:
            raise ValueError(f"llm_models.steps.{step}.model 必须是模型名或 null")
        reasoning = step_value["reasoning"]
        if not isinstance(reasoning, bool):
            raise ValueError(f"llm_models.steps.{step}.reasoning 必须是布尔值")
        normalized_steps[step] = {
            "model": normalized_model,
            "reasoning": reasoning,
        }
    return {"default": default.strip(), "steps": normalized_steps}


def validate_crawl_sources(value: Any, *, allow_daily: bool = False) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("crawl_sources 必须是有序列表")
    return normalize_source_list([str(item) for item in value], allow_daily=allow_daily)


SECTION_VALIDATORS = {
    "llm_models": validate_llm_models,
    "crawl_sources": validate_crawl_sources,
}


def validate_section(section: str, value: Any) -> Any:
    validator = SECTION_VALIDATORS.get(section)
    if validator is None:
        raise ValueError(f"未知配置分区：{section}")
    return validator(value)


def resolve_llm_steps(value: Mapping[str, Any]) -> dict[str, LLMStepConfig]:
    normalized = validate_llm_models(value)
    default = normalized["default"]
    return {
        step: LLMStepConfig(
            model=normalized["steps"][step]["model"] or default,
            reasoning=normalized["steps"][step]["reasoning"],
        )
        for step in LLM_STEPS
    }


def resolve_models(value: Mapping[str, Any]) -> dict[str, str]:
    return {
        step: config.model
        for step, config in resolve_llm_steps(value).items()
    }


def load_business_config(adapter: Optional[Any] = None) -> BusinessConfig:
    if adapter is None:
        from src.adapters.db_postgres_core import get_adapter

        adapter = get_adapter()
    rows = adapter.app_config.fetch_settings()
    indexed = {str(row["section"]): row for row in rows}
    missing = [section for section in SETTING_SECTIONS if section not in indexed]
    if missing:
        raise BusinessConfigError(f"数据库缺少业务配置分区：{', '.join(missing)}")
    try:
        llm_value = validate_llm_models(indexed["llm_models"]["value"])
        sources = validate_crawl_sources(indexed["crawl_sources"]["value"])
    except ValueError as exc:
        raise BusinessConfigError(f"数据库业务配置无效：{exc}") from exc

    account_rows = adapter.app_config.fetch_enabled_accounts()
    accounts: dict[str, list[CrawlAccount]] = {
        source: [] for source in ACCOUNT_SOURCES
    }
    for row in account_rows:
        source = str(row["source"])
        if source not in accounts:
            continue
        accounts[source].append(
            CrawlAccount(
                id=str(row["id"]),
                source=source,
                normalized_identifier=str(row["normalized_identifier"]),
                original_input=str(row["original_input"]),
                profile_url=str(row["profile_url"]),
                display_name=row.get("display_name"),
            )
        )
    return BusinessConfig(
        llm_steps=resolve_llm_steps(llm_value),
        crawl_sources=tuple(sources),
        accounts={key: tuple(items) for key, items in accounts.items()},
        versions={
            section: int(indexed[section]["version"])
            for section in SETTING_SECTIONS
        },
    )


def get_business_config() -> BusinessConfig:
    current = _CURRENT_CONFIG.get()
    return current if current is not None else load_business_config()


def get_model_for_step(step: str) -> str:
    return get_business_config().model_for(step)


def get_llm_step_config(step: str) -> LLMStepConfig:
    return get_business_config().step_config(step)


@contextmanager
def business_config_context(config: BusinessConfig) -> Iterator[BusinessConfig]:
    token = _CURRENT_CONFIG.set(config)
    try:
        yield config
    finally:
        _CURRENT_CONFIG.reset(token)


LEGACY_ENV_KEYS = (
    "CRAWL_SOURCES",
    "LLM_MODEL",
    "LLM_SUMMARY_MODEL",
    "LLM_SOURCE_MODEL",
    "LLM_SENTIMENT_MODEL",
    "LLM_SCORING_MODEL",
    "LLM_EXTERNAL_FILTER_MODEL",
    "LLM_BEIJING_GATE_MODEL",
    "LLM_DUPLICATE_REVIEW_MODEL",
    "LLM_REASONING_ENABLED",
    "LLM_SUMMARY_REASONING_ENABLED",
    "LLM_SOURCE_REASONING_ENABLED",
    "LLM_SENTIMENT_REASONING_ENABLED",
    "TOUTIAO_AUTHORS_PATH",
    "TENCENT_AUTHORS_PATH",
    "BTIME_UIDS_PATH",
    "BEIJINGHAO_COLUMNS_PATH",
)
LEGACY_ACCOUNT_FILES = (
    Path("config/toutiao_author.txt"),
    Path("config/qq_author.txt"),
    Path("config/btime_author.txt"),
    Path("config/beijinghao_author.txt"),
    Path("newsqq_crawl/qq_author.txt"),
)


def warn_legacy_config(*, root: Optional[Path] = None) -> list[str]:
    from src.config import load_environment

    load_environment()
    repository_root = root or Path(__file__).resolve().parents[1]
    warnings: list[str] = []
    for key in LEGACY_ENV_KEYS:
        if os.getenv(key) is not None:
            warnings.append(f"旧配置 {key} 已不再生效，请在控制台设置页修改")
    for relative_path in LEGACY_ACCOUNT_FILES:
        path = repository_root / relative_path
        if path.exists():
            warnings.append(f"旧配置文件 {relative_path.as_posix()} 已不再生效，请在控制台设置页修改")
    for message in warnings:
        logging.getLogger(__name__).warning(message)
    return warnings


__all__ = [
    "ACCOUNT_SOURCES",
    "BusinessConfig",
    "BusinessConfigError",
    "CrawlAccount",
    "LEGACY_ACCOUNT_FILES",
    "LEGACY_ENV_KEYS",
    "LLMStepConfig",
    "LLM_STEPS",
    "LLM_STEP_LABELS",
    "SECTION_VALIDATORS",
    "SETTING_SECTIONS",
    "SOURCE_ALIASES",
    "SOURCE_BY_KEY",
    "SOURCE_CATALOG",
    "business_config_context",
    "get_business_config",
    "get_llm_step_config",
    "get_model_for_step",
    "load_business_config",
    "normalize_source_key",
    "normalize_source_list",
    "resolve_models",
    "resolve_llm_steps",
    "validate_crawl_sources",
    "validate_llm_models",
    "validate_section",
    "warn_legacy_config",
]
