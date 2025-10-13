from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

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


@dataclass(frozen=True)
class Settings:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: Optional[str]
    db_schema: str
    siliconflow_base_url: str
    siliconflow_api_key: Optional[str]
    summarize_model_name: str
    source_model_name: str
    score_model_name: str
    siliconflow_enable_thinking: bool
    process_limit: Optional[int]
    default_concurrency: int
    keywords_path: Path
    console_basic_username: Optional[str]
    console_basic_password: Optional[str]
    console_api_token: Optional[str]
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]
    feishu_receive_id: Optional[str]
    feishu_receive_id_type: str
    beijing_keywords_path: Path


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

    siliconflow_base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    siliconflow_api_key = os.getenv("SILICONFLOW_API_KEY")
    summarize_model_name = os.getenv("SUMMARIZE_MODEL_NAME", os.getenv("MODEL_NAME", "Qwen/Qwen2.5-14B-Instruct"))
    source_model_name = os.getenv("SOURCE_MODEL_NAME", summarize_model_name)
    score_model_name = os.getenv("SCORE_MODEL_NAME", os.getenv("MODEL_NAME", "Qwen/Qwen2.5-14B-Instruct"))
    siliconflow_enable_thinking = _bool_from_env(os.getenv("ENABLE_THINKING"), default=False)

    process_limit = _optional_int(os.getenv("PROCESS_LIMIT"))
    default_concurrency = _optional_int(os.getenv("CONCURRENCY")) or 5

    keywords_env = os.getenv("KEYWORDS_PATH")
    def _resolve_path(env_value: Optional[str], *, default: Path) -> Path:
        if env_value:
            raw_path = Path(env_value).expanduser()
            return raw_path if raw_path.is_absolute() else (_REPO_ROOT / raw_path)
        return default

    keywords_path = _resolve_path(os.getenv("KEYWORDS_PATH"), default=_REPO_ROOT / "education_keywords.txt")
    beijing_keywords_path = _resolve_path(
        os.getenv("BEIJING_KEYWORDS_PATH"),
        default=_REPO_ROOT / "data" / "beijing_keywords.txt",
    )

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

    return Settings(
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=db_user,
        db_password=db_password,
        db_schema=db_schema,
        siliconflow_base_url=siliconflow_base_url,
        siliconflow_api_key=siliconflow_api_key,
        summarize_model_name=summarize_model_name,
        source_model_name=source_model_name,
        score_model_name=score_model_name,
        siliconflow_enable_thinking=siliconflow_enable_thinking,
        process_limit=process_limit,
        default_concurrency=default_concurrency,
        keywords_path=keywords_path,
        console_basic_username=console_basic_username,
        console_basic_password=console_basic_password,
        console_api_token=console_api_token,
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        feishu_receive_id=feishu_receive_id,
        feishu_receive_id_type=feishu_receive_id_type,
        beijing_keywords_path=beijing_keywords_path,
    )


__all__ = ["Settings", "get_settings", "load_environment"]
