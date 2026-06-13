from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_LOADED = False
_ENV_FILES = (
    _REPO_ROOT / ".env.local",
    _REPO_ROOT / ".env",
    _REPO_ROOT / "config" / "abstract.env",
)


def _load_env_file(path: Path) -> None:
    """Best-effort `.env` loader that respects already-set variables."""
    if not path.exists():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        # Gracefully ignore malformed env files; explicit env vars win anyway.
        pass


def load_environment() -> None:
    """Load environment files once, preferring explicitly exported values."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    for candidate in _ENV_FILES:
        _load_env_file(candidate)
    _ENV_LOADED = True



def _get_env(*keys: str) -> Optional[str]:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value
    return None


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bool_from_env(value: Optional[str], *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_keyword_bonus_rules(raw: Optional[str]) -> Optional[Dict[str, int]]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    result: Dict[str, int] = {}
    for key, value in data.items():
        if not key:
            continue
        try:
            result[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return result or None


def _parse_keyword_bonus_rules_file(path: Path) -> Optional[Dict[str, int]]:
    if not path.exists():
        return None
    try:
        return _parse_keyword_bonus_rules(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: Optional[str]
    db_schema: str
    llm_api_base_url: str
    llm_api_key: Optional[str]
    llm_api_http_referer: Optional[str]
    llm_api_title: Optional[str]
    llm_summary_model: str
    llm_source_model: str
    llm_scoring_model: str
    llm_sentiment_model: str
    llm_reasoning_enabled: bool
    llm_reasoning_effort: Optional[str]
    llm_reasoning_max_tokens: Optional[int]
    llm_reasoning_exclude: bool
    llm_summary_reasoning_enabled: bool
    llm_source_reasoning_enabled: bool
    llm_sentiment_reasoning_enabled: bool
    llm_scoring_timeout: int
    llm_summary_timeout: int
    llm_external_filter_timeout: int
    llm_beijing_gate_timeout: int
    llm_quota_alert_enabled: bool
    llm_quota_alert_cooldown_seconds: int
    llm_quota_alert_state_path: Path
    score_promotion_threshold: int
    process_limit: Optional[int]
    default_concurrency: int
    summary_concurrency: int
    keywords_path: Path
    console_basic_username: Optional[str]
    console_basic_password: Optional[str]
    console_api_token: Optional[str]
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]
    feishu_receive_id: Optional[str]
    feishu_receive_id_type: str
    beijing_keywords_path: Path
    score_keyword_bonus_rules: Dict[str, int]
    llm_external_filter_model: str
    external_filter_threshold: int
    external_filter_negative_threshold: int
    internal_filter_threshold: int
    internal_filter_negative_threshold: int
    internal_filter_prompt_path: Path
    external_filter_batch_size: int
    external_filter_max_retries: int
    llm_beijing_gate_model: str
    beijing_gate_max_retries: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached project settings sourced from env variables."""
    load_environment()

    db_host = _get_env("DB_HOST", "POSTGRES_HOST") or "localhost"
    db_port = _optional_int(_get_env("DB_PORT", "POSTGRES_PORT")) or 5432
    db_name = _get_env("DB_NAME", "POSTGRES_DB", "POSTGRES_DB_NAME") or "postgres"
    db_user = _get_env("DB_USER", "POSTGRES_USER") or "postgres"
    db_password = _get_env("DB_PASSWORD", "POSTGRES_PASSWORD")
    db_schema = _get_env("DB_SCHEMA", "POSTGRES_SCHEMA") or "public"

    default_llm_model = os.getenv("LLM_MODEL") or "deepseek/deepseek-v4-flash"
    llm_api_base_url = os.getenv("LLM_API_BASE_URL") or "https://openrouter.ai/api/v1"
    llm_api_key = os.getenv("LLM_API_KEY")
    llm_api_http_referer = os.getenv("LLM_API_HTTP_REFERER")
    llm_api_title = os.getenv("LLM_API_TITLE")
    llm_summary_model = os.getenv("LLM_SUMMARY_MODEL") or default_llm_model
    llm_source_model = os.getenv("LLM_SOURCE_MODEL") or llm_summary_model
    llm_scoring_model = os.getenv("LLM_SCORING_MODEL") or default_llm_model
    llm_sentiment_model = os.getenv("LLM_SENTIMENT_MODEL") or llm_summary_model
    llm_reasoning_enabled = _bool_from_env(
        os.getenv("LLM_REASONING_ENABLED"),
        default=True,
    )
    raw_reasoning_effort = (os.getenv("LLM_REASONING_EFFORT") or "").strip().lower()
    llm_reasoning_effort = raw_reasoning_effort if raw_reasoning_effort in {"low", "medium", "high"} else None
    llm_reasoning_max_tokens = _optional_int(os.getenv("LLM_REASONING_MAX_TOKENS"))
    llm_reasoning_exclude = _bool_from_env(
        os.getenv("LLM_REASONING_EXCLUDE"),
        default=True,
    )
    llm_summary_reasoning_enabled = _bool_from_env(
        os.getenv("LLM_SUMMARY_REASONING_ENABLED"),
        default=False,
    )
    llm_source_reasoning_enabled = _bool_from_env(
        os.getenv("LLM_SOURCE_REASONING_ENABLED"),
        default=True,
    )
    llm_sentiment_reasoning_enabled = _bool_from_env(
        os.getenv("LLM_SENTIMENT_REASONING_ENABLED"),
        default=True,
    )

    # LLM timeout configuration (in seconds)
    llm_global_timeout = _optional_int(os.getenv("LLM_TIMEOUT")) or 90
    llm_scoring_timeout = _optional_int(os.getenv("LLM_SCORING_TIMEOUT")) or llm_global_timeout or 30
    llm_summary_timeout = (
        _optional_int(os.getenv("LLM_SUMMARY_TIMEOUT")) or llm_global_timeout or 60
    )
    llm_external_filter_timeout = (
        _optional_int(os.getenv("LLM_EXTERNAL_FILTER_TIMEOUT")) or llm_global_timeout or 30
    )
    llm_beijing_gate_timeout = (
        _optional_int(os.getenv("LLM_BEIJING_GATE_TIMEOUT")) or llm_global_timeout or 60
    )
    llm_quota_alert_enabled = _bool_from_env(
        os.getenv("LLM_QUOTA_ALERT_ENABLED"),
        default=True,
    )
    llm_quota_alert_cooldown_seconds = (
        _optional_int(os.getenv("LLM_QUOTA_ALERT_COOLDOWN_SECONDS")) or 21600
    )
    llm_external_filter_model = os.getenv("LLM_EXTERNAL_FILTER_MODEL") or llm_scoring_model
    raw_score_threshold = _optional_int(
        _get_env("SCORE_PROMOTION_THRESHOLD", "SCORE_THRESHOLD")
    )
    score_promotion_threshold = raw_score_threshold if raw_score_threshold is not None else 60
    raw_external_threshold = _optional_int(os.getenv("EXTERNAL_FILTER_THRESHOLD"))
    external_filter_threshold = raw_external_threshold if raw_external_threshold is not None else 20
    raw_external_negative_threshold = _optional_int(os.getenv("EXTERNAL_FILTER_NEGATIVE_THRESHOLD"))
    external_filter_negative_threshold = (
        raw_external_negative_threshold if raw_external_negative_threshold is not None else external_filter_threshold
    )
    raw_internal_threshold = _optional_int(os.getenv("INTERNAL_FILTER_THRESHOLD"))
    internal_filter_threshold = (
        raw_internal_threshold if raw_internal_threshold is not None else external_filter_threshold
    )
    raw_internal_negative_threshold = _optional_int(os.getenv("INTERNAL_FILTER_NEGATIVE_THRESHOLD"))
    internal_filter_negative_threshold = (
        raw_internal_negative_threshold
        if raw_internal_negative_threshold is not None
        else internal_filter_threshold
    )
    external_filter_batch_size = _optional_int(os.getenv("EXTERNAL_FILTER_BATCH_SIZE")) or 50
    external_filter_max_retries = _optional_int(os.getenv("EXTERNAL_FILTER_MAX_RETRIES")) or 3
    llm_beijing_gate_model = os.getenv("LLM_BEIJING_GATE_MODEL") or llm_scoring_model
    beijing_gate_max_retries = _optional_int(os.getenv("BEIJING_GATE_MAX_RETRIES")) or 3

    process_limit = _optional_int(os.getenv("PROCESS_LIMIT"))
    default_concurrency = _optional_int(os.getenv("CONCURRENCY")) or 50
    summary_concurrency = _optional_int(os.getenv("SUMMARY_CONCURRENCY")) or default_concurrency

    def _resolve_path(env_value: Optional[str], *, default: Path) -> Path:
        if env_value:
            raw_path = Path(env_value).expanduser()
            return raw_path if raw_path.is_absolute() else (_REPO_ROOT / raw_path)
        return default

    config_dir = _REPO_ROOT / "config"

    raw_keywords_env = os.getenv("KEYWORDS_PATH")
    keywords_path = _resolve_path(
        raw_keywords_env,
        default=config_dir / "education_keywords.txt",
    )

    raw_beijing_env = os.getenv("BEIJING_KEYWORDS_PATH")
    beijing_keywords_path = _resolve_path(
        raw_beijing_env,
        default=config_dir / "beijing_keywords.txt",
    )

    raw_internal_prompt = os.getenv("INTERNAL_FILTER_PROMPT_PATH")
    internal_filter_prompt_path = _resolve_path(
        raw_internal_prompt,
        default=_REPO_ROOT / "docs" / "internal_importance_prompt.md",
    )

    raw_quota_alert_state_path = os.getenv("LLM_QUOTA_ALERT_STATE_PATH")
    llm_quota_alert_state_path = _resolve_path(
        raw_quota_alert_state_path,
        default=_REPO_ROOT / "logs" / "llm_quota_alert_state.json",
    )

    keyword_bonus_rules = _parse_keyword_bonus_rules(os.getenv("SCORE_KEYWORD_BONUSES"))
    raw_bonus_path_env = os.getenv("SCORE_KEYWORD_BONUSES_PATH")
    keyword_bonus_rules_path = _resolve_path(
        raw_bonus_path_env,
        default=config_dir / "score_keyword_bonuses.json",
    )
    if keyword_bonus_rules is None:
        parsed = _parse_keyword_bonus_rules_file(keyword_bonus_rules_path)
        keyword_bonus_rules = parsed
    if keyword_bonus_rules is None:
        keyword_bonus_rules = {}

    console_basic_username = os.getenv("CONSOLE_BASIC_USERNAME")
    console_basic_password = os.getenv("CONSOLE_BASIC_PASSWORD")
    console_api_token = os.getenv("CONSOLE_API_TOKEN")

    feishu_app_id = _get_env("FEISHU_APP_ID", "feishu_APP_ID")
    feishu_app_secret = _get_env("FEISHU_APP_SECRET", "feishu_APP_Secret")
    feishu_receive_id = _get_env("FEISHU_RECEIVE_ID", "FEISHU_OPEN_ID", "my_open_id")
    feishu_receive_id_type = os.getenv("FEISHU_RECEIVE_ID_TYPE", "open_id")
    if feishu_receive_id_type not in {"open_id", "user_id", "union_id"}:
        feishu_receive_id_type = "open_id"

    keywords_path = keywords_path.resolve()
    beijing_keywords_path = beijing_keywords_path.resolve()
    internal_filter_prompt_path = internal_filter_prompt_path.resolve()
    llm_quota_alert_state_path = llm_quota_alert_state_path.resolve()

    return Settings(
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_schema=db_schema,
        llm_api_base_url=llm_api_base_url,
        llm_api_key=llm_api_key,
        llm_api_http_referer=llm_api_http_referer,
        llm_api_title=llm_api_title,
        llm_summary_model=llm_summary_model,
        llm_source_model=llm_source_model,
        llm_scoring_model=llm_scoring_model,
        llm_sentiment_model=llm_sentiment_model,
        llm_reasoning_enabled=llm_reasoning_enabled,
        llm_reasoning_effort=llm_reasoning_effort,
        llm_reasoning_max_tokens=llm_reasoning_max_tokens,
        llm_reasoning_exclude=llm_reasoning_exclude,
        llm_summary_reasoning_enabled=llm_summary_reasoning_enabled,
        llm_source_reasoning_enabled=llm_source_reasoning_enabled,
        llm_sentiment_reasoning_enabled=llm_sentiment_reasoning_enabled,
        llm_scoring_timeout=llm_scoring_timeout,
        llm_summary_timeout=llm_summary_timeout,
        llm_external_filter_timeout=llm_external_filter_timeout,
        llm_beijing_gate_timeout=llm_beijing_gate_timeout,
        llm_quota_alert_enabled=llm_quota_alert_enabled,
        llm_quota_alert_cooldown_seconds=llm_quota_alert_cooldown_seconds,
        llm_quota_alert_state_path=llm_quota_alert_state_path,
        score_promotion_threshold=score_promotion_threshold,
        process_limit=process_limit,
        default_concurrency=default_concurrency,
        summary_concurrency=summary_concurrency,
        keywords_path=keywords_path,
        console_basic_username=console_basic_username,
        console_basic_password=console_basic_password,
        console_api_token=console_api_token,
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        feishu_receive_id=feishu_receive_id,
        feishu_receive_id_type=feishu_receive_id_type,
        beijing_keywords_path=beijing_keywords_path,
        score_keyword_bonus_rules=keyword_bonus_rules,
        llm_external_filter_model=llm_external_filter_model,
        external_filter_threshold=external_filter_threshold,
        external_filter_negative_threshold=external_filter_negative_threshold,
        internal_filter_threshold=internal_filter_threshold,
        internal_filter_negative_threshold=internal_filter_negative_threshold,
        internal_filter_prompt_path=internal_filter_prompt_path,
        external_filter_batch_size=external_filter_batch_size,
        external_filter_max_retries=external_filter_max_retries,
        llm_beijing_gate_model=llm_beijing_gate_model,
        beijing_gate_max_retries=beijing_gate_max_retries,
    )


__all__ = ["Settings", "get_settings", "load_environment"]
