from __future__ import annotations

from typing import Any

from src.adapters.db_postgres import get_adapter as _postgres_get_adapter

_ADAPTER: Any = None


def get_adapter():
    global _ADAPTER
    if _ADAPTER is None:
        _ADAPTER = _postgres_get_adapter()
    return _ADAPTER


__all__ = ["get_adapter"]
