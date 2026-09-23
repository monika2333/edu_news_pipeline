from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from src.domain.source_aliases import SourceAliasRules


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
API_STYLE_OPENROUTER = "openrouter"
API_STYLE_THINKING = "thinking"
LLM_API_STYLES = (API_STYLE_OPENROUTER, API_STYLE_THINKING)
ENDPOINT_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LLM_API_KEY_ENV_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*_API_KEY$")
ENDPOINT_ITEM_FIELDS = frozenset(
    {
        "key",
        "label",
        "base_url",
        "api_key_env",
        "api_style",
        "temperature_override",
    }
)


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
    SourceDefinition("xinhua", "新华网"),
    SourceDefinition("stdaily", "科技日报"),
    SourceDefinition("bbtnews", "北京商报"),
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
SETTING_SECTIONS = (
    "llm_endpoints",
    "llm_models",
    "crawl_sources",
    "score_keyword_bonuses",
    "education_keywords",
    "beijing_keywords",
    "source_aliases",
    "review_sort_keywords",
)
SCORE_BONUS_MIN = -100
SCORE_BONUS_MAX = 100
# 审阅页「自动排序」的固定分类桶。键同时是设置页与嵌入前端词表的类别名；
# 「其他」是兜底桶，不是可配置类别。
REVIEW_SORT_CATEGORIES = ("市教委", "中小学", "高校")
# 初始词表，源自原 review_tab_sort.js 的 CATEGORY_RULES（去掉其中重复的
# 「市教委」）。只用于迁移播种和页面回退值，日常修改走控制台设置页。
REVIEW_SORT_KEYWORD_DEFAULTS: dict[str, tuple[str, ...]] = {
    "市教委": (
        "市教委",
        "市教委教育工委",
        "教工委",
        "教育工委",
        "教育委员",
        "首都教育两委",
        "教育两委",
    ),
    "中小学": (
        "中小学",
        "小学",
        "初中",
        "高中",
        "义务教育",
        "基础教育",
        "幼儿园",
        "幼儿",
        "托育",
        "k12",
        "班主任",
        "青少年",
        "少儿",
        "少年",
    ),
    "高校": (
        "高校",
        "大学",
        "学院",
        "本科",
        "研究生",
        "硕士",
        "博士",
    ),
}


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
    endpoint: Optional[str] = None


@dataclass(frozen=True)
class LLMEndpointConfig:
    key: str
    label: str
    base_url: str
    api_key_env: str
    api_style: str
    temperature_override: Optional[float]


@dataclass(frozen=True)
class ScoreKeywordBonus:
    keyword: str
    bonus: int


@dataclass(frozen=True)
class BusinessConfig:
    llm_steps: Mapping[str, LLMStepConfig]
    crawl_sources: tuple[str, ...]
    accounts: Mapping[str, tuple[CrawlAccount, ...]]
    versions: Mapping[str, int]
    llm_endpoints: Mapping[str, LLMEndpointConfig] = field(default_factory=dict)
    default_endpoint: str = ""
    score_keyword_bonuses: tuple[ScoreKeywordBonus, ...] = ()
    education_keywords: tuple[str, ...] = ()
    beijing_keywords: tuple[str, ...] = ()
    source_aliases: "SourceAliasRules" = field(default_factory=lambda: SourceAliasRules())
    review_sort_keywords: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def model_for(self, step: str) -> str:
        return self.step_config(step).model

    def step_config(self, step: str) -> LLMStepConfig:
        try:
            return self.llm_steps[step]
        except KeyError as exc:
            raise BusinessConfigError(f"缺少模型配置步骤：{step}") from exc

    def score_bonus_rules(self) -> dict[str, int]:
        """Keyword→bonus rules in the order they were configured."""

        return {item.keyword: item.bonus for item in self.score_keyword_bonuses}

    def endpoint_by_key(self, key: Optional[str]) -> LLMEndpointConfig:
        resolved = key or self.default_endpoint
        if not resolved:
            raise BusinessConfigError(
                "未配置默认接入点：数据库缺少 llm_endpoints 分区或其内容为空"
            )
        try:
            return self.llm_endpoints[resolved]
        except KeyError as exc:
            raise BusinessConfigError(f"接入点不存在：{resolved}") from exc

    def endpoint_for_step(self, step: str) -> LLMEndpointConfig:
        return self.endpoint_by_key(self.step_config(step).endpoint)

    def resolved_endpoint_key(self, step: str) -> str:
        step_config = self.step_config(step)
        return step_config.endpoint or self.default_endpoint

    def with_crawl_sources(self, sources: Sequence[str]) -> "BusinessConfig":
        normalized = normalize_source_list(sources, allow_daily=True)
        return replace(self, crawl_sources=tuple(normalized))

    def snapshot(self) -> dict[str, Any]:
        return {
            "llm_models": {
                step: {
                    "model": config.model,
                    "reasoning": config.reasoning,
                    "endpoint": self.resolved_endpoint_key(step),
                }
                for step, config in self.llm_steps.items()
            },
            "llm_endpoints": {
                item.key: {
                    "base_url": item.base_url,
                    "api_style": item.api_style,
                }
                for item in self.llm_endpoints.values()
            },
            "crawl_sources": list(self.crawl_sources),
            "crawl_accounts": {
                source: [item.normalized_identifier for item in items]
                for source, items in self.accounts.items()
            },
            "score_keyword_bonuses": [
                {"keyword": item.keyword, "bonus": item.bonus}
                for item in self.score_keyword_bonuses
            ],
            "education_keywords": list(self.education_keywords),
            "beijing_keywords": list(self.beijing_keywords),
            "source_aliases": {
                "suffixes": list(self.source_aliases.suffixes),
                "aliases": dict(self.source_aliases.aliases),
            },
            "review_sort_keywords": {
                category: list(rules)
                for category, rules in self.review_sort_keywords.items()
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
        unexpected_step_fields = set(step_value) - {"model", "reasoning", "endpoint"}
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
        raw_endpoint = step_value.get("endpoint")
        if raw_endpoint is None:
            endpoint: Optional[str] = None
        elif isinstance(raw_endpoint, str) and raw_endpoint.strip():
            endpoint = raw_endpoint.strip()
        else:
            raise ValueError(f"llm_models.steps.{step}.endpoint 必须是接入点 key 或 null")
        if endpoint is not None and normalized_model is None:
            raise ValueError(
                f"llm_models.steps.{step} 指定了 endpoint 时 model 不能为空："
                "模型名与接入点服务商绑定，不能继承默认模型"
            )
        normalized_steps[step] = {
            "model": normalized_model,
            "reasoning": reasoning,
            "endpoint": endpoint,
        }
    return {"default": default.strip(), "steps": normalized_steps}


def validate_endpoint_base_url(value: Any, *, allowed_hosts: Sequence[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_url 必须是非空字符串")
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("base_url 必须是非空字符串")
    parts = urlsplit(normalized)
    if parts.scheme != "https":
        raise ValueError("base_url 必须使用 https")
    if parts.query or parts.fragment:
        raise ValueError("base_url 不能携带 query 或 fragment")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("base_url 缺少主机名")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("base_url 端口无效") from exc
    if port is not None and port != 443:
        raise ValueError("base_url 只允许省略端口或使用 443 端口")
    allowed = {item.strip().lower() for item in allowed_hosts if item and item.strip()}
    if host not in allowed:
        raise ValueError(
            f"base_url 主机 {host} 不在 LLM_ALLOWED_HOSTS 白名单内，"
            "如需新增服务商请先在 .env 的 LLM_ALLOWED_HOSTS 中加入其域名"
        )
    return normalized


def validate_llm_endpoints(value: Any) -> dict[str, Any]:
    from src.config import get_settings

    if not isinstance(value, Mapping):
        raise ValueError("llm_endpoints 必须是对象")
    unexpected = set(value) - {"default", "items"}
    if unexpected:
        raise ValueError(f"llm_endpoints 含未知字段：{', '.join(sorted(unexpected))}")
    items = value.get("items")
    if not isinstance(items, list):
        raise ValueError("llm_endpoints.items 必须是数组")
    if not 1 <= len(items) <= 10:
        raise ValueError("llm_endpoints.items 数量必须在 1 到 10 之间")
    allowed_hosts = get_settings().llm_allowed_hosts
    normalized_items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"llm_endpoints.items[{index}]"
        if not isinstance(item, Mapping):
            raise ValueError(f"{prefix} 必须是对象")
        unexpected_fields = set(item) - ENDPOINT_ITEM_FIELDS
        if unexpected_fields:
            fields = ", ".join(sorted(unexpected_fields))
            raise ValueError(f"{prefix} 含未知字段：{fields}")
        key = item.get("key")
        if not isinstance(key, str) or not ENDPOINT_KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"{prefix}.key 只能是小写字母开头的 1-32 位小写字母、数字、连字符或下划线"
            )
        if key in seen_keys:
            raise ValueError(f"{prefix} key 重复：{key}")
        seen_keys.add(key)
        label = item.get("label")
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
            raise ValueError(f"{prefix}.label 必须是 1-40 字符")
        base_url = validate_endpoint_base_url(
            item.get("base_url"),
            allowed_hosts=allowed_hosts,
        )
        api_key_env = item.get("api_key_env")
        if not isinstance(api_key_env, str) or not LLM_API_KEY_ENV_PATTERN.fullmatch(
            api_key_env
        ):
            raise ValueError(
                f"{prefix}.api_key_env 必须是以 _API_KEY 结尾的环境变量名，"
                "防止误指向其他敏感变量"
            )
        api_style = item.get("api_style")
        if api_style not in LLM_API_STYLES:
            raise ValueError(f"{prefix}.api_style 必须是 openrouter 或 thinking")
        temperature = item.get("temperature_override")
        if temperature is not None:
            if isinstance(temperature, bool) or not isinstance(
                temperature, (int, float)
            ):
                raise ValueError(f"{prefix}.temperature_override 必须是 null 或 0-2 的数值")
            if not 0 <= float(temperature) <= 2:
                raise ValueError(f"{prefix}.temperature_override 取值必须在 0 到 2 之间")
        normalized_items.append(
            {
                "key": key,
                "label": label.strip(),
                "base_url": base_url,
                "api_key_env": api_key_env,
                "api_style": api_style,
                "temperature_override": temperature,
            }
        )
    default = value.get("default")
    if not isinstance(default, str) or default not in seen_keys:
        raise ValueError("llm_endpoints.default 必须指向 items 中已存在的 key")
    return {"default": default, "items": normalized_items}


def validate_crawl_sources(value: Any, *, allow_daily: bool = False) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("crawl_sources 必须是有序列表")
    normalized = normalize_source_list(
        [str(item) for item in value],
        allow_daily=allow_daily,
    )
    catalog_index = {
        definition.key: index
        for index, definition in enumerate(SOURCE_CATALOG)
    }
    return sorted(normalized, key=catalog_index.__getitem__)


def validate_score_keyword_bonuses(value: Any) -> list[dict[str, Any]]:
    """Validate the ordered keyword-bonus list.

    Stored as a list (not an object) because jsonb does not preserve object
    key order, and both the settings page and ``matched_rules`` depend on the
    configured order.
    """

    if not isinstance(value, list):
        raise ValueError(
            "score_keyword_bonuses 必须是有序数组，不能存为 JSON 对象"
            "（jsonb 不保留对象键顺序）"
        )
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"score_keyword_bonuses[{index}] 必须是对象")
        unexpected = set(item) - {"keyword", "bonus"}
        if unexpected:
            fields = ", ".join(sorted(str(field) for field in unexpected))
            raise ValueError(f"score_keyword_bonuses[{index}] 含未知字段：{fields}")
        keyword = item.get("keyword")
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError(f"score_keyword_bonuses[{index}].keyword 不能为空")
        bonus = item.get("bonus")
        if isinstance(bonus, bool) or not isinstance(bonus, int):
            raise ValueError(
                f"score_keyword_bonuses[{index}].bonus 必须是整数，布尔值不算"
            )
        if not SCORE_BONUS_MIN <= bonus <= SCORE_BONUS_MAX:
            raise ValueError(
                f"score_keyword_bonuses[{index}].bonus 取值必须在 "
                f"{SCORE_BONUS_MIN} 到 {SCORE_BONUS_MAX} 之间"
            )
        keyword = keyword.strip()
        if keyword in seen:
            raise ValueError(f"score_keyword_bonuses 关键词重复：{keyword}")
        seen.add(keyword)
        normalized.append({"keyword": keyword, "bonus": bonus})
    return normalized


def validate_keyword_list(value: Any, *, label: str) -> list[str]:
    """Normalize a keyword list; reject it when nothing survives.

    An empty list would silently flip behavior at the consumer: education
    keywords gate the crawl (empty = admit everything), Beijing keywords
    drive local routing (empty = classify everything as outside Beijing).
    """

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} 必须是字符串列表")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, str):
            raise ValueError(f"{label}[{index}] 必须是字符串")
        token = raw.strip()
        if not token:
            continue
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    if not normalized:
        raise ValueError(
            f"{label} 规范化后不能为空：空词表会静默改变线上筛选行为"
        )
    return normalized


def validate_education_keywords(value: Any) -> list[str]:
    return validate_keyword_list(value, label="education_keywords")


def validate_beijing_keywords(value: Any) -> list[str]:
    return validate_keyword_list(value, label="beijing_keywords")


def validate_source_aliases(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("source_aliases 必须是对象")
    unexpected = set(value) - {"suffixes", "aliases"}
    if unexpected:
        raise ValueError(f"source_aliases 含未知字段：{', '.join(sorted(unexpected))}")
    suffixes = value.get("suffixes")
    aliases = value.get("aliases")
    if not isinstance(suffixes, list):
        raise ValueError("source_aliases.suffixes 必须是有序列表（剥离顺序有意义）")
    if not isinstance(aliases, Mapping):
        raise ValueError("source_aliases.aliases 必须是对象")
    normalized_suffixes: list[str] = []
    for index, suffix in enumerate(suffixes):
        if not isinstance(suffix, str) or not suffix:
            raise ValueError(f"source_aliases.suffixes[{index}] 必须是非空字符串")
        normalized_suffixes.append(suffix)
    normalized_aliases: dict[str, str] = {}
    for key, target in aliases.items():
        if not isinstance(key, str) or not key:
            raise ValueError("source_aliases.aliases 的键必须是非空字符串")
        if not isinstance(target, str) or not target:
            raise ValueError(f"source_aliases.aliases[{key}] 的值必须是非空字符串")
        normalized_aliases[key] = target
    return {"suffixes": normalized_suffixes, "aliases": normalized_aliases}


def resolve_source_aliases(value: Any) -> SourceAliasRules:
    normalized = validate_source_aliases(value)
    return SourceAliasRules(
        suffixes=tuple(normalized["suffixes"]),
        aliases=dict(normalized["aliases"]),
    )


def validate_review_sort_keywords(value: Any) -> dict[str, list[str]]:
    """Validate the review-page auto-sort category keyword rules.

    键固定为三个展示类别（「其他」是兜底桶，不配置）；值是关键词列表，
    允许留空——某类留空只意味着该类什么都匹配不到。查重按 toLowerCase 后的
    词进行：前端匹配对大小写不敏感，"K12" 与 "k12" 语义相同。跨类别重复
    直接拒绝：分类按固定优先级首个命中即归类，低优先级类别里的重复词
    永远不生效，属于死配置。
    """

    if not isinstance(value, Mapping):
        raise ValueError("review_sort_keywords 必须是对象")
    unexpected = sorted(str(key) for key in value if key not in REVIEW_SORT_CATEGORIES)
    if unexpected:
        raise ValueError(
            f"review_sort_keywords 含未知类别：{', '.join(unexpected)}"
        )
    missing = [category for category in REVIEW_SORT_CATEGORIES if category not in value]
    if missing:
        raise ValueError(f"review_sort_keywords 缺少类别：{'、'.join(missing)}")
    owner: dict[str, str] = {}
    normalized: dict[str, list[str]] = {}
    for category in REVIEW_SORT_CATEGORIES:
        items = value[category]
        if not isinstance(items, list):
            raise ValueError(f"review_sort_keywords.{category} 必须是有序数组")
        words: list[str] = []
        for index, item in enumerate(items):
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"review_sort_keywords.{category}[{index}] 不能为空"
                )
            word = item.strip()
            lowered = word.lower()
            if lowered in {candidate.lower() for candidate in words}:
                raise ValueError(f"review_sort_keywords.{category} 关键词重复：{word}")
            previous = owner.get(lowered)
            if previous is not None:
                raise ValueError(
                    f"关键词「{word}」同时出现在 {previous} 和 {category}："
                    "分类按优先级首个命中，低优先级里的重复词不会生效，请只保留一处"
                )
            owner[lowered] = category
            words.append(word)
        normalized[category] = words
    return normalized


def resolve_review_sort_keywords(value: Any | None) -> dict[str, list[str]]:
    """Validated rules for the review page, falling back to seeded defaults.

    供审阅页渲染路径使用：分区行缺失（迁移未执行）或内容损坏时回退默认
    词表。排序只是展示层功能，不能因它阻断整个页面。
    """

    if value is not None:
        try:
            return validate_review_sort_keywords(value)
        except ValueError as exc:
            logging.getLogger(__name__).warning(
                "review_sort_keywords 分区无效，审阅页回退默认词表：%s", exc
            )
    return {
        category: list(rules)
        for category, rules in REVIEW_SORT_KEYWORD_DEFAULTS.items()
    }


SECTION_VALIDATORS = {
    "llm_endpoints": validate_llm_endpoints,
    "llm_models": validate_llm_models,
    "crawl_sources": validate_crawl_sources,
    "score_keyword_bonuses": validate_score_keyword_bonuses,
    "education_keywords": validate_education_keywords,
    "beijing_keywords": validate_beijing_keywords,
    "source_aliases": validate_source_aliases,
    "review_sort_keywords": validate_review_sort_keywords,
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
            endpoint=normalized["steps"][step]["endpoint"],
        )
        for step in LLM_STEPS
    }


def resolve_llm_endpoints(
    value: Mapping[str, Any],
) -> tuple[dict[str, LLMEndpointConfig], str]:
    normalized = validate_llm_endpoints(value)
    items = {
        item["key"]: LLMEndpointConfig(
            key=item["key"],
            label=item["label"],
            base_url=item["base_url"],
            api_key_env=item["api_key_env"],
            api_style=item["api_style"],
            temperature_override=item["temperature_override"],
        )
        for item in normalized["items"]
    }
    return items, normalized["default"]


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
        llm_endpoints, default_endpoint = resolve_llm_endpoints(
            indexed["llm_endpoints"]["value"]
        )
        sources = validate_crawl_sources(indexed["crawl_sources"]["value"])
        bonuses = validate_score_keyword_bonuses(
            indexed["score_keyword_bonuses"]["value"]
        )
        education_keywords = validate_education_keywords(
            indexed["education_keywords"]["value"]
        )
        beijing_keywords = validate_beijing_keywords(
            indexed["beijing_keywords"]["value"]
        )
        source_aliases = resolve_source_aliases(indexed["source_aliases"]["value"])
        review_sort_keywords = validate_review_sort_keywords(
            indexed["review_sort_keywords"]["value"]
        )
    except ValueError as exc:
        raise BusinessConfigError(f"数据库业务配置无效：{exc}") from exc
    endpoint_keys = set(llm_endpoints)
    for step in LLM_STEPS:
        endpoint_ref = llm_value["steps"][step]["endpoint"]
        if endpoint_ref is not None and endpoint_ref not in endpoint_keys:
            raise BusinessConfigError(
                f"数据库业务配置无效：{LLM_STEP_LABELS[step]}引用了不存在的接入点：{endpoint_ref}"
            )

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
        llm_endpoints=llm_endpoints,
        default_endpoint=default_endpoint,
        score_keyword_bonuses=tuple(
            ScoreKeywordBonus(keyword=item["keyword"], bonus=item["bonus"])
            for item in bonuses
        ),
        education_keywords=tuple(education_keywords),
        beijing_keywords=tuple(beijing_keywords),
        source_aliases=source_aliases,
        review_sort_keywords={
            category: tuple(words)
            for category, words in review_sort_keywords.items()
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
    "LLM_API_BASE_URL",
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
    "KEYWORDS_PATH",
    "BEIJING_KEYWORDS_PATH",
    "SOURCE_ALIASES_PATH",
    "SCORE_KEYWORD_BONUSES",
    "SCORE_KEYWORD_BONUSES_PATH",
)
LEGACY_CONFIG_FILES = (
    Path("config/toutiao_author.txt"),
    Path("config/qq_author.txt"),
    Path("config/btime_author.txt"),
    Path("config/beijinghao_author.txt"),
    Path("newsqq_crawl/qq_author.txt"),
    Path("config/education_keywords.txt"),
    Path("config/beijing_keywords.txt"),
    Path("config/source_aliases.json"),
    Path("config/score_keyword_bonuses.json"),
)


def warn_legacy_config(*, root: Optional[Path] = None) -> list[str]:
    from src.config import load_environment

    load_environment()
    repository_root = root or Path(__file__).resolve().parents[1]
    warnings: list[str] = []
    for key in LEGACY_ENV_KEYS:
        if os.getenv(key) is not None:
            warnings.append(f"旧配置 {key} 已不再生效，请在控制台设置页修改")
    for relative_path in LEGACY_CONFIG_FILES:
        path = repository_root / relative_path
        if path.exists():
            warnings.append(f"旧配置文件 {relative_path.as_posix()} 已不再生效，请在控制台设置页修改")
    for message in warnings:
        logging.getLogger(__name__).warning(message)
    return warnings


__all__ = [
    "ACCOUNT_SOURCES",
    "API_STYLE_OPENROUTER",
    "API_STYLE_THINKING",
    "BusinessConfig",
    "BusinessConfigError",
    "CrawlAccount",
    "ENDPOINT_KEY_PATTERN",
    "ENDPOINT_ITEM_FIELDS",
    "LEGACY_CONFIG_FILES",
    "LEGACY_ENV_KEYS",
    "LLMStepConfig",
    "LLMEndpointConfig",
    "LLM_API_KEY_ENV_PATTERN",
    "LLM_API_STYLES",
    "LLM_STEPS",
    "LLM_STEP_LABELS",
    "REVIEW_SORT_CATEGORIES",
    "REVIEW_SORT_KEYWORD_DEFAULTS",
    "SCORE_BONUS_MAX",
    "SCORE_BONUS_MIN",
    "ScoreKeywordBonus",
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
    "resolve_llm_endpoints",
    "resolve_models",
    "resolve_llm_steps",
    "resolve_review_sort_keywords",
    "resolve_source_aliases",
    "validate_beijing_keywords",
    "validate_crawl_sources",
    "validate_education_keywords",
    "validate_endpoint_base_url",
    "validate_keyword_list",
    "validate_llm_endpoints",
    "validate_llm_models",
    "validate_review_sort_keywords",
    "validate_score_keyword_bonuses",
    "validate_section",
    "validate_source_aliases",
    "warn_legacy_config",
]
