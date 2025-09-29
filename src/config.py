from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env.local"


def _load_env_file() -> None:
    """Load .env.local into os.environ if present.

    Values already set in the environment take precedence so this stays safe
    when running in production where secrets may arrive from the host.
    """
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str
    openai_api_key: Optional[str] = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_env_file()
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
    return Settings(
        supabase_url=supabase_url,
        supabase_key=supabase_key,
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
    )
