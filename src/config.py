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
    supabase_url: Optional[str]
    supabase_service_role_key: Optional[str]
    supabase_key: Optional[str]
    supabase_anon_key: Optional[str]
    supabase_db_host: Optional[str]
    supabase_db_port: int
    supabase_db_name: str
    supabase_db_user: str
    supabase_db_password: Optional[str]
    supabase_db_schema: str
    siliconflow_base_url: str
    siliconflow_api_key: Optional[str]
    siliconflow_model_name: str
    siliconflow_enable_thinking: bool
    process_limit: Optional[int]
    default_concurrency: int
    keywords_path: Path

    @property
    def effective_supabase_key(self) -> Optional[str]:
        """Return the most privileged Supabase key available."""
        return (
            self.supabase_service_role_key
            or self.supabase_key
            or self.supabase_anon_key
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached project settings sourced from env variables."""
    load_environment()

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase_key = os.getenv("SUPABASE_KEY")
    supabase_anon_key = os.getenv("SUPABASE_ANON_KEY")

    supabase_db_host = os.getenv("SUPABASE_DB_HOST")
    supabase_db_port = _optional_int(os.getenv("SUPABASE_DB_PORT")) or 5432
    supabase_db_name = os.getenv("SUPABASE_DB_NAME", "postgres")
    supabase_db_user = os.getenv("SUPABASE_DB_USER", "postgres")
    supabase_db_password = os.getenv("SUPABASE_DB_PASSWORD")
    supabase_db_schema = os.getenv("SUPABASE_DB_SCHEMA", "public")

    siliconflow_base_url = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
    siliconflow_api_key = os.getenv("SILICONFLOW_API_KEY")
    siliconflow_model_name = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-14B-Instruct")
    siliconflow_enable_thinking = _bool_from_env(os.getenv("ENABLE_THINKING"), default=False)

    process_limit = _optional_int(os.getenv("PROCESS_LIMIT"))
    default_concurrency = _optional_int(os.getenv("CONCURRENCY")) or 5

    keywords_env = os.getenv("KEYWORDS_PATH")
    if keywords_env:
        raw_path = Path(keywords_env).expanduser()
        keywords_path = raw_path if raw_path.is_absolute() else (_REPO_ROOT / raw_path)
    else:
        keywords_path = _REPO_ROOT / "education_keywords.txt"

    keywords_path = keywords_path.resolve()

    return Settings(
        supabase_url=supabase_url,
        supabase_service_role_key=supabase_service_role_key,
        supabase_key=supabase_key,
        supabase_anon_key=supabase_anon_key,
        supabase_db_host=supabase_db_host,
        supabase_db_port=supabase_db_port,
        supabase_db_name=supabase_db_name,
        supabase_db_user=supabase_db_user,
        supabase_db_password=supabase_db_password,
        supabase_db_schema=supabase_db_schema,
        siliconflow_base_url=siliconflow_base_url,
        siliconflow_api_key=siliconflow_api_key,
        siliconflow_model_name=siliconflow_model_name,
        siliconflow_enable_thinking=siliconflow_enable_thinking,
        process_limit=process_limit,
        default_concurrency=default_concurrency,
        keywords_path=keywords_path,
    )


__all__ = ["Settings", "get_settings", "load_environment"]





