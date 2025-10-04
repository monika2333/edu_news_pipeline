from __future__ import annotations

from typing import Any

from src.config import get_settings

_ADAPTER: Any = None


def _load_supabase_adapter():
    from src.adapters.db_supabase import get_adapter as supabase_get_adapter  # lazy import

    return supabase_get_adapter()


def _load_postgres_adapter():
    from src.adapters.db_postgres import get_adapter as postgres_get_adapter

    return postgres_get_adapter()


def get_adapter():
    global _ADAPTER
    settings = get_settings()
    backend = (settings.db_backend or "postgres").lower()
    if backend not in {"postgres", "supabase"}:
        backend = "postgres"
    if _ADAPTER is not None:
        return _ADAPTER
    if backend == "supabase":
        _ADAPTER = _load_supabase_adapter()
    else:
        _ADAPTER = _load_postgres_adapter()
    return _ADAPTER


__all__ = ["get_adapter"]
